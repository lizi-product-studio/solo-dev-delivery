import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sensitivity_cli', Path(__file__).resolve().parents[1]/'scripts/challenge.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

PROGRAM = '''class Flow:
    def __init__(self): self.ready = True
    def change(self): self.ready = False
    def recover(self): self.ready = True
    def use(self):
        if not self.ready: return 'denied'
        return 'applied'
'''
WEAK = '''import unittest
from flow import Flow
class Business(unittest.TestCase):
    def test_chain(self):
        f = Flow(); f.change()
        self.assertFalse(f.ready)
'''
STRONG = '''import unittest
from flow import Flow
class Business(unittest.TestCase):
    def test_chain(self):
        f = Flow(); self.assertEqual(f.use(), 'applied')
        f.change(); self.assertEqual(f.use(), 'denied')
        f.recover(); self.assertEqual(f.use(), 'applied')
'''


class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.work = self.root/'candidate'; self.work.mkdir()
        self.control = self.root/'control'; self.control.mkdir()
        (self.work/'goal.txt').write_text('After change, use must be denied until recovery; then use works again.')
        (self.work/'flow.py').write_text(PROGRAM)
        self.test = self.control/'checks.py'; self.test.write_text(WEAK)
        self.probes = self.control/'risks.json'
        self.item = {'id': 'downstream', 'basis': 'goal.txt:1 after change use denied until recovery',
                     'risk': 'Downstream use ignores the changed precondition', 'test': 'Business.test_chain',
                     'patches': [{'path':'flow.py','before':'if not self.ready:', 'after':'if False:'}]}
        self.save()

    def save(self, items=None):
        self.probes.write_text(json.dumps({'probes':items or [self.item]}))

    def verify(self, name='proof.json', explicit=True):
        return cli.verify(self.work,['goal.txt'],['flow.py'],self.test,self.control/name,5,
                          self.probes if explicit else None)

    def test_full_green_suite_still_blocks_when_downstream_fault_survives(self):
        result = self.verify()
        self.assertEqual(('INSUFFICIENT','SURVIVED'), (result['status'],result['probes'][0]['observation']))
        proof=json.loads(Path(result['receipt']).read_text())
        self.assertEqual('CHALLENGE_PASS',json.loads(Path(proof['base_receipt']).read_text())['status'])
        self.assertEqual(PROGRAM,(self.work/'flow.py').read_text())

    def test_real_chain_distinguishes_fault_and_reuses_without_execution(self):
        self.test.write_text(STRONG)
        first=self.verify(); self.assertEqual('SENSITIVITY_PASS',first['status'])
        with patch.object(cli,'run',side_effect=AssertionError('must reuse')):
            again=self.verify('another.json',explicit=False)
        self.assertEqual((True,0,first['receipt']), (again['reused'],again['executed_tests'],again['receipt']))
        self.assertEqual('SENSITIVITY_PASS',cli.status(first['receipt'])['status'])

    def test_no_flag_does_not_drop_known_surviving_risk_and_test_fix_closes_it(self):
        first=self.verify(); raw=Path(first['receipt']).read_bytes()
        omitted=self.verify('forgotten.json',explicit=False)
        self.assertEqual(('INSUFFICIENT',True),(omitted['status'],omitted['reused']))
        self.test.write_text(STRONG)
        self.assertEqual('STALE',cli.status(first['receipt'])['status'])
        fixed=self.verify('fixed.json',explicit=False)
        self.assertEqual('SENSITIVITY_PASS',fixed['status'])
        self.assertEqual(raw,Path(first['receipt']).read_bytes())

    def test_missing_observation_defers_later_work_instead_of_claiming_coverage(self):
        self.item['test']='Business.test_missing'; second=dict(self.item,id='later',test='Business.test_chain')
        self.save([self.item,second]); result=self.verify()
        self.assertEqual(['MISSING_BASE_OBSERVATION','DEFERRED_AFTER_GAP'],[p['observation'] for p in result['probes']])
        self.assertEqual(1,result['executed_tests'])

    def test_compile_failure_or_test_error_is_not_a_business_refutation(self):
        self.test.write_text(STRONG)
        self.item['patches'][0]['after']='if ???:';self.save()
        self.assertEqual('INVALID_PROBE',self.verify()['probes'][0]['observation'])
        self.item['patches'][0]['after']='if 1/0:';self.save()
        self.assertEqual('INVALID_OR_UNKNOWN',self.verify('runtime.json')['probes'][0]['observation'])

    def test_no_mutating_policy_or_nonunique_patch_can_satisfy_probe(self):
        self.test.write_text(STRONG)
        self.item['patches'][0].update(path='goal.txt',before='After',after='Before');self.save()
        self.assertEqual('INVALID_PROBE',self.verify()['probes'][0]['observation'])
        self.item['patches'][0].update(path='flow.py',before='self.ready',after='False');self.save()
        self.assertEqual('INVALID_PROBE',self.verify('duplicate.json')['probes'][0]['observation'])

    def test_missing_selected_spec_and_changed_spec_invalidate_stop_evidence(self):
        self.test.write_text(STRONG);result=self.verify()
        self.item['risk']='A different rationale';self.save()
        self.assertEqual('STALE',cli.status(result['receipt'])['status'])
        self.probes.unlink()
        with self.assertRaisesRegex(ValueError,'selected probe spec missing'):
            self.verify('removed.json',explicit=False)

    def test_counterfactual_fail_does_not_pollute_actual_candidate_failures(self):
        self.test.write_text(STRONG);result=self.verify();proof=json.loads(Path(result['receipt']).read_text())
        mutant=json.loads(Path(proof['executions'][-1]).read_text())
        self.assertEqual(('local-counterfactual-result/1','FAIL'),(mutant['format'],mutant['status']))
        suite=cli._verify_suite(self.work,['goal.txt'],['flow.py'],self.test,self.control/'reuse-suite.json',5)
        self.assertEqual(('CHALLENGE_PASS',True),(suite['status'],suite['reused']))


if __name__=='__main__': unittest.main()
