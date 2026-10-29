import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('challenge_cli', KIT / 'scripts/challenge.py')
challenge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(challenge)


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        (self.work / 'policy.txt').write_text('difference must preserve sign')
        (self.work / 'candidate.py').write_text('def difference(a,b): return a-b\n')
        self.packet = self.root / 'review/packet.json'
        self.test = self.root / 'review/test_business.py'
        self.receipt = self.root / 'review/attempt.json'
        challenge.freeze(self.work, ['policy.txt'], ['candidate.py'], self.packet)
        self.test.write_text('import unittest\nfrom candidate import difference\nclass Check(unittest.TestCase):\n def test_sign(self): self.assertEqual(-2,difference(3,5))\n')

    def run_challenge(self):
        return challenge.run(self.packet, self.test, self.receipt, 5)

    def verify(self, name='verify.json'):
        return challenge.verify(self.work, ['policy.txt'], ['candidate.py'], self.test,
                                self.root/'review'/name, 5)

    def test_one_call_verification_reuses_exact_evidence_without_execution(self):
        first = self.verify()
        self.assertEqual(('CHALLENGE_PASS', False, 1),
                         (first['status'], first['reused'], first['executed_tests']))
        raw = Path(first['receipt']).read_bytes()
        with patch.object(challenge, 'run', side_effect=AssertionError('must not repeat')):
            second = self.verify('unnecessary-copy.json')
        self.assertEqual((True, 0, first['receipt']),
                         (second['reused'], second['executed_tests'], second['receipt']))
        self.assertEqual(raw, Path(first['receipt']).read_bytes())
        self.assertFalse((self.root/'review/unnecessary-copy.json').exists())

    def test_partial_pass_does_not_replace_necessary_full_suite(self):
        self.test.write_text('import unittest\nfrom candidate import difference\nclass Check(unittest.TestCase):\n def test_normal(self): self.assertEqual(2,difference(5,3))\n def test_reverse(self): self.assertEqual(99,difference(3,5))\n')
        challenge.run(self.packet, self.test, self.receipt, 5, ['Check.test_normal'])
        result = self.verify()
        self.assertEqual(('FAIL', False, 2),
                         (result['status'], result['reused'], result['executed_tests']))
        self.assertEqual(['Check.test_reverse'], result['failed_tests'])

    def test_current_failure_dominates_later_pass_even_if_omitted(self):
        gate = self.root/'environment-flag'
        self.test.write_text('import unittest\nfrom pathlib import Path\nclass Check(unittest.TestCase):\n def test_gate(self): self.assertTrue(Path('+repr(str(gate))+').exists())\n')
        self.assertEqual('FAIL', self.run_challenge()['status'])
        gate.touch()
        passed = self.root/'review/later-pass.json'
        self.assertEqual('CHALLENGE_PASS', challenge.run(self.packet, self.test, passed, 5)['status'])
        with patch.object(challenge, 'run', side_effect=AssertionError('known failure needs diagnosis')):
            result = self.verify()
        self.assertEqual(('FAIL', True), (result['status'], result['reused']))
        self.assertEqual(str(self.receipt.resolve()), result['receipt'])

    def test_changed_candidate_or_script_requires_new_evidence_preserving_old(self):
        first = self.verify()
        saved = Path(first['receipt']).read_bytes()
        (self.work/'candidate.py').write_text('def difference(a,b): return abs(a-b)\n')
        with self.assertRaisesRegex(ValueError, 'not reusable'):
            self.verify()
        self.assertEqual('FAIL', self.verify('changed.json')['status'])
        self.assertEqual('STALE', challenge.status(first['receipt'])['status'])
        self.assertEqual(saved, Path(first['receipt']).read_bytes())
        self.test.write_text('import unittest\nfrom candidate import difference\nclass Check(unittest.TestCase):\n def test_revised(self): self.assertEqual(2,difference(3,5))\n')
        result = self.verify('changed-script.json')
        self.assertEqual(('CHALLENGE_PASS', False), (result['status'], result['reused']))

    def test_unknown_is_not_cached_as_completed_acceptance(self):
        self.test.write_text('import unittest\nclass Check(unittest.TestCase):\n @unittest.skip("missing environment")\n def test_skip(self): pass\n')
        self.assertEqual('UNKNOWN', self.verify()['status'])
        repeated = self.verify('second.json')
        self.assertEqual(('UNKNOWN', False), (repeated['status'], repeated['reused']))

    def test_compact_output_retains_full_execution_log_on_disk(self):
        self.test.write_text(self.test.read_text()+'\nprint("detail" * 3000)\n')
        result = self.verify()
        self.assertLess(len(json.dumps(result)), 1000)
        receipt = json.loads(Path(result['receipt']).read_text())
        self.assertGreater(receipt['stdout']['bytes'], 18000)
        self.assertEqual('CHALLENGE_PASS', challenge.status(result['receipt'])['status'])

    def test_observed_pass_is_bound_and_the_original_is_not_executed(self):
        self.assertEqual('CHALLENGE_PASS', self.run_challenge()['status'])
        self.assertEqual('CHALLENGE_PASS', challenge.status(self.receipt)['status'])
        self.assertEqual('def difference(a,b): return a-b\n', (self.work/'candidate.py').read_text())
        self.assertEqual(1, json.loads(self.receipt.read_text())['test_summary']['tests'])

    def test_actual_business_failure_keeps_receipt_and_cannot_be_overwritten(self):
        # New authorized candidate, rather than editing an already frozen one.
        (self.work/'candidate.py').write_text('def difference(a,b): return abs(a-b)\n')
        self.packet = self.root / 'review/new-packet.json'
        challenge.freeze(self.work, ['policy.txt'], ['candidate.py'], self.packet)
        result = self.run_challenge()
        self.assertEqual('FAIL', result['status'])
        self.assertEqual(1, result['test_summary']['failures'])
        self.assertIn('AssertionError', result['stderr']['text'])
        with self.assertRaises(ValueError): self.run_challenge()

    def test_candidate_input_and_review_script_changes_invalidate_old_pass(self):
        self.run_challenge()
        for path in (self.work/'candidate.py', self.work/'policy.txt', self.test, self.packet):
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            self.assertEqual('STALE', challenge.status(self.receipt)['status'])
            path.write_bytes(original)
        (self.work/'candidate.py').unlink()
        self.assertEqual('STALE', challenge.status(self.receipt)['status'])

    def test_mutated_candidate_cannot_be_replayed_as_the_frozen_candidate(self):
        (self.work/'candidate.py').write_text('def difference(a,b): return 0\n')
        with self.assertRaisesRegex(ValueError, 'STALE_CANDIDATE'):
            self.run_challenge()
        self.assertFalse(self.receipt.exists())

    def test_zero_tests_and_skipped_checks_do_not_pass(self):
        self.test.write_text('print("PASS")\n')
        self.assertEqual('UNKNOWN', self.run_challenge()['status'])
        self.receipt = self.root / 'review/skipped.json'
        self.test.write_text('import unittest\nclass Check(unittest.TestCase):\n @unittest.skip("environment missing")\n def test_missing(self): pass\n')
        self.assertEqual('UNKNOWN', self.run_challenge()['status'])

    def test_control_and_reviewer_cannot_be_in_the_candidate_workspace(self):
        with self.assertRaises(ValueError):
            challenge.freeze(self.work, ['policy.txt'], ['candidate.py'], self.work/'packet.json')
        own_test = self.work/'test_self.py'
        own_test.write_text(self.test.read_text())
        with self.assertRaises(ValueError):
            challenge.run(self.packet, own_test, self.receipt, 5)

    def test_reviewer_temporary_writes_do_not_change_candidate_outputs(self):
        self.test.write_text('import unittest\nfrom pathlib import Path\nclass Check(unittest.TestCase):\n def test_disposable(self):\n  Path("candidate.py").write_text("temporary change")\n  self.assertTrue(Path("candidate.py").exists())\n')
        self.assertEqual('CHALLENGE_PASS', self.run_challenge()['status'])
        self.assertEqual('def difference(a,b): return a-b\n', (self.work/'candidate.py').read_text())

    def test_rendered_block_passes_and_a_later_wrong_person_fails(self):
        from core.local_checks import run_check
        (self.work/'rows.json').write_text('{"rows":[{"job":"ticket","person":"Lin","amount":null}]}')
        columns=[{'label':'Job','pointer':['job']},{'label':'Person','pointer':['person']},{'label':'Amount','pointer':['amount']}]
        challenge.render(self.work, 'rows.json', ['rows'], columns, 'work', 'handoff.md')
        probe={'probe':{'type':'json.table','path':'handoff.md','source':'rows.json','rows':['rows'],'columns':columns,'block':'work'}}
        self.assertEqual('PASS',run_check(self.work,probe,['rows.json','handoff.md'])['verdict'])
        self.assertIn('未知',(self.work/'handoff.md').read_text(encoding='utf-8'))
        path=self.work/'handoff.md'
        path.write_text(path.read_text(encoding='utf-8').replace('Lin','Chen'), encoding='utf-8')
        self.assertEqual('FAIL',run_check(self.work,probe,['rows.json','handoff.md'])['verdict'])

    def test_one_shot_relations_rejects_unknown_total_and_stales_on_source_change(self):
        source = self.work/'summary.json'
        source.write_text('{"rows":[{"amount":2},{"amount":null}],"total":2,"known":2}')
        spec = self.root/'review/relations.json'
        spec.write_text(json.dumps({'scope':['summary.json'],'checks':[{'id':'sum','probe':{'type':'json.aggregate','path':'summary.json','pointer':['total'],'rows':['rows'],'value':['amount'],'known_sum':['known']}}]}))
        receipt = self.root/'review/relations-run1.json'
        self.assertEqual('FAIL',challenge.check_relations(self.work,spec,receipt)['status'])
        source.write_text('{"rows":[{"amount":2},{"amount":null}],"total":null,"known":2}')
        self.assertEqual('STALE',challenge.status(receipt)['status'])
        receipt = self.root/'review/relations-run2.json'
        self.assertEqual('RELATIONS_PASS',challenge.check_relations(self.work,spec,receipt)['status'])
        self.assertEqual('RELATIONS_PASS',challenge.status(receipt)['status'])
        source.write_text(source.read_text().replace('"known":2','"known":3'))
        self.assertEqual('STALE',challenge.status(receipt)['status'])

    def test_individually_passing_checks_on_different_file_versions_are_stale(self):
        source=self.work/'value.json'
        source.write_text('{"n":1}')
        spec=self.root/'review/drift-spec.json'
        spec.write_text(json.dumps({'scope':['value.json'],'checks':[{'id':'one','probe':{'type':'json.value','path':'value.json','pointer':['n'],'expected':1}},{'id':'two','probe':{'type':'json.value','path':'value.json','pointer':['n'],'expected':2}}]}))
        actual=challenge.run_check
        def change_after_first(*args):
            result=actual(*args)
            if args[1]['id']=='one':source.write_text('{"n":2}')
            return result
        with patch.object(challenge,'run_check',side_effect=change_after_first):
            result=challenge.check_relations(self.work,spec,self.root/'review/drift-receipt.json')
        self.assertEqual(['PASS','PASS'],[x['verdict'] for x in result['checks']])
        self.assertEqual('STALE',result['status'])


if __name__ == '__main__':
    unittest.main()
