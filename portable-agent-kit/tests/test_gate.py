"""Real execution gate regressions; no model, dependency or wall-clock worker limit."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import gate


class DeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.home = self.root / 'host'
        self.control = self.root / 'review'
        self.task = self.control / 'task.json'
        (self.work / 'request.md').write_text('1. Save a value and read it back.\n2. Reject a negative value without changing it.\n')
        gate.start(self.work, ['request.md'], ['candidate.py'], self.task, 'session', self.home, decision_mode=False)
        self.test = self.control / 'checks.py'
        self.test.write_text('import unittest\nimport candidate\nclass Business(unittest.TestCase):\n def test_save(self): self.assertEqual(candidate.save(7), 7)\n def test_reject(self):\n  with self.assertRaises(ValueError): candidate.save(-1)\n')
        self.plan = self.control / 'plan.json'
        brief = gate.read(self.control / 'brief.json')
        self.assertEqual(len(brief['units']), 2)
        self.plan.write_text(json.dumps({'test_file': str(self.test), 'outcomes':[
            {'id':'saved','sources':['request.md:1'],'expected':'saved value is readable'},
            {'id':'reject','sources':['request.md:2'],'expected':'negative rejected'}], 'checks':[
            {'id':'save','test':'Business.test_save','covers':['saved']},
            {'id':'reject','test':'Business.test_reject','covers':['reject']}]}))
        self.payload = {'hook_event_name':'Stop','session_id':'session','cwd':str(self.work),'stop_hook_active':False}

    def seal(self):
        return gate.seal(self.task, self.plan)

    def implement(self, correct=True):
        body = 'def save(v):\n'
        if correct:
            body += ' if v < 0: raise ValueError("negative")\n'
        (self.work / 'candidate.py').write_text(body + ' return v\n')

    def test_no_registered_task_does_not_block_normal_chat(self):
        p = dict(self.payload, session_id='another')
        self.assertEqual(gate.hook(p,self.home),{})

    def test_unsealed_source_check_selection_blocks_finish(self):
        result=gate.hook(self.payload,self.home)
        self.assertEqual(result['decision'],'block')
        self.assertIn('SELECT_SOURCE_BUSINESS_SCENARIOS',result['reason'])

    def test_missing_source_obligation_cannot_be_sealed(self):
        p=gate.read(self.plan)
        p['outcomes'].pop()
        p['checks'].pop()
        self.plan.write_text(json.dumps(p))
        with self.assertRaisesRegex(ValueError,'unaccounted'):
            self.seal()

    def test_implementation_before_source_acceptance_cannot_be_relabelled_test_first(self):
        self.implement()
        with self.assertRaisesRegex(ValueError,'before implementation'):
            self.seal()

    def test_source_brief_cannot_omit_original_clause(self):
        b=gate.read(self.control/'brief.json')
        b['units'].pop()
        gate.save(self.control/'brief.json',b)
        with self.assertRaisesRegex(ValueError,'differs from original'):
            self.seal()

    def test_stop_blocks_unmeasured_then_actual_checks_allow_and_reuse(self):
        self.seal()
        self.implement()
        before=gate.hook(self.payload,self.home)
        self.assertEqual(before['decision'],'block')
        self.assertIn('missing_outcomes',before['reason'])
        result=gate.check(self.task,True)
        self.assertEqual(result['status'],'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.assertEqual(set(result['executed_checks']),{'save','reject'})
        self.assertEqual(gate.hook(self.payload,self.home),{})
        self.assertEqual(gate.check(self.task,True)['executed_checks'],[])

    def test_normal_pass_does_not_replace_missing_boundary_observation(self):
        self.seal()
        self.implement()
        gate.check(self.task,False)
        report=gate.check(self.task,False)
        packet=Path(report['packet'])
        gate.challenge.run(packet,self.test,packet.parent/'partial.json',5,['Business.test_save'])
        result=gate.check(self.task,False)
        self.assertEqual(result['missing_outcomes'],['reject'])
        self.assertEqual(result['missing_checks'],['reject'])
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_actual_failure_stays_visible_then_repaired_bytes_get_rechecked(self):
        self.seal()
        self.implement(False)
        result=gate.check(self.task,True)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['failed_outcomes'],['reject'])
        self.assertEqual(gate.check(self.task,True)['executed_checks'],[])
        self.assertEqual(gate.hook(dict(self.payload,stop_hook_active=True),self.home)['decision'],'block')
        self.implement(True)
        result=gate.check(self.task,True)
        self.assertEqual(result['status'],'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        receipts=list(self.control.glob('runs/*/*-replay.json'))
        self.assertEqual(len(receipts),2)
        self.assertIn('FAIL',{gate.read(p)['status'] for p in receipts})

    def test_output_edit_after_pass_revokes_stop_permission(self):
        self.seal()
        self.implement()
        gate.check(self.task,True)
        self.implement(False)
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_test_edit_after_pass_requires_explicit_source_reconciliation(self):
        self.seal()
        self.implement()
        gate.check(self.task,True)
        self.test.write_text(self.test.read_text()+'\n# changed acceptance\n')
        self.assertEqual(gate.check(self.task)['next_action'],'RECONCILE_CHANGED_ACCEPTANCE_WITH_SOURCE')
        with self.assertRaisesRegex(ValueError,'source-based reason'):
            self.seal()
        gate.seal(self.task,self.plan,'Clarify source expectation; retain all previous checks.')
        self.assertEqual(gate.check(self.task,True)['status'],'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.assertTrue((self.control/'scope-0/checks.py').exists())

    def test_changed_or_missing_source_fails_closed(self):
        self.seal()
        self.implement()
        gate.check(self.task,True)
        (self.work/'request.md').write_text('Changed source')
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')
        (self.work/'request.md').unlink()
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_malformed_registration_fails_closed_without_crashing(self):
        gate.registry(self.home,'session').write_text('{}')
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_successful_stop_closes_registration_so_later_chat_is_not_rechecked(self):
        self.seal()
        self.implement()
        gate.check(self.task,True)
        self.assertEqual(gate.hook(self.payload,self.home),{})
        self.implement(False)
        self.assertEqual(gate.hook(self.payload,self.home),{})
        self.assertEqual(gate.read(self.task)['state'],'DELIVERED')

    def test_external_blocker_is_not_reported_as_delivery_complete(self):
        evidence=self.control/'external-error.txt'
        evidence.write_text('Local environment unavailable; connection refused.')
        task=gate.read(self.task)
        task.update(state='BLOCKED_EXTERNAL',blocker={'reason':'environment unavailable','evidence':gate.digest(evidence)})
        gate.save(self.task,task)
        result=gate.hook(self.payload,self.home)
        self.assertIn('BLOCKED, not complete',result['systemMessage'])
        self.assertEqual(gate.read(self.task)['state'],'BLOCKED_EXTERNAL')
        evidence.unlink()
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_install_is_idempotent_and_preserves_other_hook_definitions(self):
        self.home.mkdir(exist_ok=True)
        path=self.home/'hooks.json'
        gate.save(path,{'description':'keep me','hooks':{'Stop':[{'hooks':[{'type':'command','command':'other-tool'}]}]}})
        gate.install_hook(self.home)
        gate.install_hook(self.home)
        result=gate.read(path)
        self.assertEqual(result['description'],'keep me')
        self.assertEqual(len(result['hooks']['Stop']),2)
        self.assertEqual(result['hooks']['Stop'][0]['hooks'][0]['command'],'other-tool')

    def test_real_process_abort_cannot_be_hidden_by_an_earlier_pass(self):
        from unittest.mock import patch
        self.test.write_text('import os\nif os.environ.get("GATE_TEST_ABORT")=="1": os._exit(1)\n'+self.test.read_text())
        self.seal()
        self.implement()
        passed=gate.check(self.task,True)
        packet=Path(passed['packet'])
        with patch.dict('os.environ',{'GATE_TEST_ABORT':'1'}):
            receipt=gate.challenge.run(packet,self.test,packet.parent/'aborted.json',5)
        self.assertEqual(receipt['status'],'UNKNOWN')
        self.assertEqual(gate.check(self.task)['status'],'BLOCKED')
        self.assertEqual(gate.hook(self.payload,self.home)['decision'],'block')

    def test_aggregate_failure_without_attributed_failed_case_is_blocking(self):
        self.seal()
        self.implement()
        passed=gate.check(self.task,True)
        packet=Path(passed['packet'])
        original=gate.read(next(packet.parent.glob('*-replay.json')))
        for suffix,cases in [('passing_cases',original['test_cases']),('no_cases',[])]:
            receipt=dict(original,status='FAIL',test_cases=cases)
            gate.challenge.write_new(packet.parent/(suffix+'.json'),receipt)
        self.assertEqual(gate.check(self.task)['status'],'BLOCKED')
