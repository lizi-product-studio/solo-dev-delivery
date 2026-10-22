"""Behavioral tests for source-accounted challenge selection and receipts."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('challenge_cli', KIT / 'scripts/challenge.py')
challenge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(challenge)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.review = self.root / 'review'
        self.review.mkdir()
        (self.work / 'policy.md').write_text(
            'The order must be saved before confirmation.\n\n'
            'On a rejected update, retain the prior order and explain the conflict.\n\n'
            'Operators may inspect the audit trail.\n', encoding='utf-8')
        (self.work / 'candidate.py').write_text('VALUE = 7\n', encoding='utf-8')
        self.brief = self.review / 'brief.json'
        challenge.brief(self.work, ['policy.md'], self.brief)
        self.packet = self.review / 'packet.json'
        challenge.freeze(self.work, ['policy.md'], ['candidate.py'], self.packet)
        self.test = self.review / 'checks.py'
        self.test.write_text(
            'import unittest, os\n'
            'class Checks(unittest.TestCase):\n'
            ' def test_saved(self): self.assertNotEqual(os.environ.get(\"BREAK_SAVED\"), \"1\")\n'
            ' def test_rejected_update(self): self.assertNotEqual(os.environ.get("BREAK_CONFLICT"), "1")\n'
            ' def test_audit(self): self.assertTrue(True)\n', encoding='utf-8')
        self.plan_path = self.review / 'plan.json'
        self.make_plan()

    def make_plan(self, include_failure=True, context_lines=(5,)):
        units = {u['line']: u['id'] for u in json.loads(self.brief.read_text())['units']}
        outcomes = [
            {'id': 'saved', 'sources': [units[1]], 'expected': 'saved order can be read back', 'priority': 3},
        ]
        checks = [{'id': 'save-check', 'test': 'Checks.test_saved', 'covers': ['saved'], 'cost': 1}]
        if include_failure:
            outcomes.append({'id': 'conflict', 'sources': [units[3]], 'expected': 'rejected update preserves old state and reports conflict', 'priority': 3})
            checks.append({'id': 'conflict-check', 'test': 'Checks.test_rejected_update', 'covers': ['conflict'], 'cost': 2})
        context = [{'source': units[line], 'reason': 'Read-only audit access is outside this candidate change.'}
                   for line in context_lines]
        self.plan_path.write_text(json.dumps({
            'test_file': str(self.test), 'outcomes': outcomes, 'checks': checks, 'context': context
        }), encoding='utf-8')

    def assess(self, evidence=(), budget=10, execute=False, report=None):
        return challenge.assess(self.brief, self.plan_path, self.packet, list(evidence), budget,
                                execute, report or self.review / 'report.json')

    def run_check(self, path=None, selected=None):
        return challenge.run(self.packet, self.test, path or self.review / 'run.json', 5, selected)

    def test_unaccounted_source_paragraph_blocks_declared_sufficiency(self):
        self.make_plan(include_failure=False, context_lines=(5,))
        report = self.assess()
        self.assertEqual('INSUFFICIENT', report['status'])
        self.assertEqual(['policy.md:3'], report['unaccounted_source_units'])
        self.assertEqual('REVIEW_UNACCOUNTED_SOURCE', report['next_action'])

    def test_context_exclusion_requires_reason_and_does_not_prove_semantics(self):
        self.make_plan(include_failure=True, context_lines=(5,))
        report = self.assess()
        self.assertEqual([], report['unaccounted_source_units'])
        self.assertEqual('INSUFFICIENT', report['status'])
        plan = json.loads(self.plan_path.read_text())
        plan['context'][0]['reason'] = '  '
        self.plan_path.write_text(json.dumps(plan), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'specific exclusion reason'):
            self.assess(report=self.review / 'bad-context.json')

    def test_zero_budget_cannot_accept_unmeasured_outcomes(self):
        report = self.assess(budget=0)
        self.assertEqual('INSUFFICIENT', report['status'])
        self.assertEqual({'saved', 'conflict'}, set(report['missing_outcomes']))
        self.assertEqual([], report['selected_checks'])
        self.assertEqual('BUDGET_OR_ENVIRONMENT_INSUFFICIENT', report['next_action'])

    def test_one_test_covers_multiple_outcomes_and_executes_once(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        p = json.loads(self.plan_path.read_text())
        p['outcomes'].append({'id': 'durable', 'sources': ['policy.md:1'], 'expected': 'order remains available after reload', 'priority': 3})
        p['checks'][0]['covers'].append('durable')
        self.plan_path.write_text(json.dumps(p), encoding='utf-8')
        result = self.run_check(selected=['Checks.test_saved'])
        self.assertEqual('CHALLENGE_PASS', result['status'])
        self.assertEqual([{'id': 'Checks.test_saved', 'status': 'PASS'}], result['test_cases'])
        report = self.assess(evidence=[self.review / 'run.json'], execute=True)
        self.assertEqual({'saved', 'durable'}, set(report['covered_outcomes']))
        self.assertEqual([], report['selected_checks'])
        self.assertEqual(['save-check'], report['completed_checks'])
        self.assertEqual([], report['missing_required_checks'])
        self.assertEqual([], report['executed_check_ids'])
        self.assertFalse((self.review / 'report-replay.json').exists())

    def test_valid_evidence_is_reused_without_reexecution(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        self.run_check(selected=['Checks.test_saved'])
        report = self.assess(evidence=[self.review / 'run.json'], execute=True)
        self.assertEqual('DECLARED_SCOPE_COVERED', report['status'])
        self.assertEqual([], report['executed_check_ids'])
        self.assertFalse((self.review / 'report-replay.json').exists())

    def test_normal_pass_does_not_cover_failure_scenario(self):
        self.make_plan(include_failure=True, context_lines=(5,))
        report = self.assess()
        self.assertEqual({'Checks.test_saved', 'Checks.test_rejected_update'}, {c['test'] for c in report['selected_checks']})
        self.run_check(selected=['Checks.test_saved'])
        after = self.assess(evidence=[self.review / 'run.json'], report=self.review / 'after-pass.json')
        self.assertEqual(['conflict'], after['missing_outcomes'])
        self.assertEqual(['Checks.test_rejected_update'], [c['test'] for c in after['selected_checks']])

    def test_skipped_empty_and_nonexistent_selected_cases_are_unknown(self):
        cases = [
            ('skip', 'import unittest\nclass Checks(unittest.TestCase):\n @unittest.skip(\"skip\")\n def test_saved(self): pass\n', ['Checks.test_saved']),
            ('empty', 'print(\"not a test\")\n', None),
            ('missing', 'import unittest\nclass Checks(unittest.TestCase):\n def test_saved(self): pass\n', ['Checks.no_such_test']),
        ]
        for label, code, selected in cases:
            with self.subTest(label=label):
                self.test.write_text(code, encoding='utf-8')
                receipt = self.review / f'{label}.json'
                result = self.run_check(path=receipt, selected=selected)
                self.assertEqual('UNKNOWN', result['status'])
                report = self.assess(evidence=[receipt], report=self.review / f'{label}-report.json')
                self.assertNotIn('saved', report['covered_outcomes'])

    def test_real_failure_dominates_pass_for_same_candidate_and_test_bytes(self):
        self.make_plan(include_failure=False)
        with patch.dict(os.environ, {'BREAK_SAVED': '1'}):
            failed = self.run_check(path=self.review / 'fail.json', selected=['Checks.test_saved'])
        passed = self.run_check(path=self.review / 'pass.json', selected=['Checks.test_saved'])
        self.assertEqual('FAIL', failed['status'])
        self.assertEqual('CHALLENGE_PASS', passed['status'])
        self.assertEqual(failed['test_sha256'], passed['test_sha256'])
        self.assertEqual(failed['packet_sha256'], passed['packet_sha256'])
        report = self.assess(evidence=[self.review / 'pass.json', self.review / 'fail.json'])
        self.assertEqual(['saved'], report['failed_outcomes'])
        self.assertNotIn('saved', report['covered_outcomes'])
        self.assertEqual('REPAIR_AND_RECHECK_RELATED_OUTCOMES', report['next_action'])

    def test_cli_assess_run_selects_and_persists_real_replay_receipt(self):
        report = self.assess(execute=True)
        replay = self.review / 'report-replay.json'
        self.assertTrue(replay.exists())
        receipt = json.loads(replay.read_text())
        self.assertEqual('local-challenge-result/1', receipt['format'])
        self.assertEqual(['Checks.test_saved', 'Checks.test_rejected_update'], [c['id'] for c in receipt['test_cases']])
        self.assertEqual(['save-check', 'conflict-check'], report['executed_check_ids'])
        self.assertIn(str(replay.resolve()), report['evidence'])
        self.assertEqual('DECLARED_SCOPE_COVERED', report['status'])

    def test_related_root_cause_expansion_uses_shared_sources_only(self):
        p = json.loads(self.plan_path.read_text())
        p['outcomes'].append({'id': 'same-root', 'sources': ['policy.md:3'], 'expected': 'conflict is explained', 'priority': 3})
        p['outcomes'].append({'id': 'unrelated', 'sources': ['policy.md:1'], 'expected': 'audit export is accurate', 'priority': 3})
        p['checks'][1]['covers'].append('same-root')
        p['checks'].append({'id': 'unrelated-check', 'test': 'Checks.test_audit', 'covers': ['unrelated'], 'cost': 1})
        self.plan_path.write_text(json.dumps(p), encoding='utf-8')
        # The shared source is line 3. Make the selected failure attributable to both outcomes.
        fail_test = self.review / 'failure.py'
        fail_test.write_text(self.test.read_text().replace(
            'def test_rejected_update(self): self.assertNotEqual(os.environ.get("BREAK_CONFLICT"), "1")',
            'def test_rejected_update(self): self.fail("counterexample")'), encoding='utf-8')
        p['test_file'] = str(fail_test)
        self.plan_path.write_text(json.dumps(p), encoding='utf-8')
        receipt = self.review / 'related-fail.json'
        challenge.run(self.packet, fail_test, receipt, 5, ['Checks.test_rejected_update'])
        report = self.assess(evidence=[receipt])
        self.assertEqual({'conflict', 'same-root'}, set(report['related_outcomes_to_review']))
        self.assertNotIn('unrelated', report['related_outcomes_to_review'])

    def test_evidence_source_plan_candidate_and_test_mutations_invalidate_results(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        self.run_check(selected=['Checks.test_saved'])
        fresh = self.review / 'fresh.json'
        self.assess(evidence=[self.review / 'run.json'], report=fresh)
        self.assertEqual('DECLARED_SCOPE_COVERED', challenge.status(fresh)['status'])
        for path in (self.work / 'policy.md', self.plan_path, self.work / 'candidate.py', self.test):
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            self.assertEqual('STALE', challenge.status(fresh)['status'], path.name)
            path.write_bytes(original)
        evidence = self.review / 'run.json'
        original = evidence.read_bytes()
        evidence.write_bytes(original + b' ')
        self.assertEqual('STALE', challenge.status(fresh)['status'], 'evidence receipt mutation')

    def test_evidence_from_a_different_packet_is_not_reused(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        extra = self.work / 'extra.py'
        extra.write_text('EXTRA = True\n', encoding='utf-8')
        other_packet = self.review / 'other-packet.json'
        challenge.freeze(self.work, ['policy.md'], ['candidate.py', 'extra.py'], other_packet)
        foreign = self.review / 'foreign.json'
        challenge.run(other_packet, self.test, foreign, 5, ['Checks.test_saved'])
        report = self.assess(evidence=[foreign])
        self.assertNotIn('saved', report['covered_outcomes'])
        self.assertEqual('different candidate packet', report['evidence_observations'][0]['reason'])

    def test_coverage_report_cannot_be_reused_as_execution_evidence(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        prior = self.review / 'prior-report.json'
        self.assess(report=prior)
        report = self.assess(evidence=[prior], report=self.review / 'next-report.json')
        self.assertNotIn('saved', report['covered_outcomes'])
        self.assertIn('not an attributable challenge execution', report['evidence_observations'][0]['reason'])

    def test_basis_change_during_selected_replay_leaves_stale_report_and_receipt(self):
        original_run = challenge.run

        def mutate_plan_after_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            self.plan_path.write_text(self.plan_path.read_text() + '\n', encoding='utf-8')
            return result

        with patch.object(challenge, 'run', side_effect=mutate_plan_after_run):
            report = self.assess(execute=True)
        replay = self.review / 'report-replay.json'
        self.assertTrue(replay.exists(), 'real replay receipt is preserved after basis drift')
        self.assertEqual('STALE', report['status'])
        self.assertEqual('RECONCILE_CHANGED_BASIS', report['next_action'])
        self.assertEqual('STALE', challenge.status(self.review / 'report.json')['status'])

    def test_auto_discovery_blocks_a_failure_omitted_from_explicit_evidence(self):
        self.make_plan(include_failure=True, context_lines=(5,))
        with patch.dict(os.environ, {'BREAK_CONFLICT': '1'}):
            challenge.run(self.packet, self.test, self.review / 'forgotten-fail.json', 5,
                          ['Checks.test_rejected_update'])
        report = self.assess(evidence=[])
        self.assertEqual(['conflict'], report['failed_outcomes'])
        self.assertEqual('REPAIR_AND_RECHECK_RELATED_OUTCOMES', report['next_action'])
        self.assertNotEqual('DECLARED_SCOPE_COVERED', report['status'])

    def test_removing_failed_method_from_plan_still_blocks_as_unmapped(self):
        self.make_plan(include_failure=True, context_lines=(5,))
        with patch.dict(os.environ, {'BREAK_CONFLICT': '1'}):
            challenge.run(self.packet, self.test, self.review / 'removed-method-fail.json', 5,
                          ['Checks.test_rejected_update'])
        plan = json.loads(self.plan_path.read_text())
        plan['checks'] = [check for check in plan['checks'] if check['id'] != 'conflict-check']
        self.plan_path.write_text(json.dumps(plan), encoding='utf-8')
        report = self.assess(evidence=[])
        self.assertEqual(['Checks.test_rejected_update'], report['unmapped_failed_tests'])
        self.assertNotEqual('DECLARED_SCOPE_COVERED', report['status'])
        self.assertEqual('REPAIR_AND_RECHECK_RELATED_OUTCOMES', report['next_action'])

    def test_new_failure_receipt_makes_prior_covered_report_stale(self):
        self.make_plan(include_failure=True, context_lines=(5,))
        passed = self.review / 'pass-before-failure.json'
        challenge.run(self.packet, self.test, passed, 5,
                      ['Checks.test_saved', 'Checks.test_rejected_update'])
        report_path = self.review / 'previously-covered.json'
        report = self.assess(evidence=[passed], report=report_path)
        self.assertEqual('DECLARED_SCOPE_COVERED', report['status'])
        self.assertEqual('DECLARED_SCOPE_COVERED', challenge.status(report_path)['status'])
        with patch.dict(os.environ, {'BREAK_CONFLICT': '1'}):
            challenge.run(self.packet, self.test, self.review / 'new-failure.json', 5,
                          ['Checks.test_rejected_update'])
        self.assertEqual('STALE', challenge.status(report_path)['status'])

    def test_normal_and_exception_checks_for_one_outcome_remain_separate_obligations(self):
        units = {u['line']: u['id'] for u in json.loads(self.brief.read_text())['units']}
        plan = {
            'test_file': str(self.test),
            'outcomes': [{'id': 'order-result', 'sources': [units[1], units[3]],
                          'expected': 'normal save works and rejected update preserves prior state', 'priority': 3}],
            'checks': [
                {'id': 'normal', 'test': 'Checks.test_saved', 'covers': ['order-result'], 'cost': 1},
                {'id': 'exception', 'test': 'Checks.test_rejected_update', 'covers': ['order-result'], 'cost': 2},
            ],
            'context': [{'source': units[5], 'reason': 'Audit inspection is outside this candidate change.'}],
        }
        self.plan_path.write_text(json.dumps(plan), encoding='utf-8')
        first = self.assess(budget=1)
        self.assertEqual(['normal'], [check['id'] for check in first['selected_checks']])
        self.assertNotEqual('DECLARED_SCOPE_COVERED', first['status'])
        self.run_check(selected=['Checks.test_saved'])
        after_normal = self.assess(evidence=[self.review / 'run.json'], budget=1,
                                   report=self.review / 'after-normal.json')
        self.assertEqual(['order-result'], after_normal['covered_outcomes'])
        self.assertEqual(['exception'], after_normal['missing_required_checks'])
        self.assertEqual([], after_normal['selected_checks'], 'remaining required check does not fit budget')
        self.assertEqual('BUDGET_OR_ENVIRONMENT_INSUFFICIENT', after_normal['next_action'])
        self.assertEqual('INSUFFICIENT', after_normal['status'])
        enough = self.assess(evidence=[self.review / 'run.json'], budget=2,
                             execute=True, report=self.review / 'enough-budget.json')
        self.assertEqual(['exception'], enough['executed_check_ids'])

    def test_optional_check_requires_a_specific_reason(self):
        self.make_plan(include_failure=False, context_lines=(3, 5))
        plan = json.loads(self.plan_path.read_text())
        plan['checks'][0]['required'] = False
        self.plan_path.write_text(json.dumps(plan), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'optional check needs a specific reason'):
            self.assess(report=self.review / 'missing-optional-reason.json')


if __name__ == '__main__':
    unittest.main()

