"""Native hook feedback uses real files, frozen tests and durable control state."""
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
sys.path.insert(1,str(ROOT/'portable-agent-kit/scripts'))
from agent_runtime.native_session import NativeSession
from native_task import install_hooks


class NativeSessionTests(unittest.TestCase):
    def setup_task(self,root,repairs=2):
        workspace=root/'workspace';workspace.mkdir();(workspace/'source.json').write_text('{"values":[20,22]}',encoding='utf-8')
        review=root/'review.py';review.write_text('import json,unittest\nfrom pathlib import Path\nclass Independent(unittest.TestCase):\n def test_recompute(self):\n  self.assertEqual(sum(json.loads(Path("source.json").read_text())["values"]),json.loads(Path("out.json").read_text())["total"])\n',encoding='utf-8')
        runtime=NativeSession(root/'native')
        runtime.hook({'hook_event_name':'UserPromptSubmit','session_id':'session','cwd':str(workspace),'prompt':'Read source.json and write the exact sum to out.json.'})
        task=runtime.begin('session',workspace,['source.json'],['out.json'],review,max_repairs=repairs)
        return runtime,workspace,review,task

    def test_cli_uses_host_session_home_without_user_filling_a_state_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,_,_,task=self.setup_task(Path(temp))
            process=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/native_task.py'),'status','--session-id','session'],capture_output=True,encoding='utf-8')
            self.assertEqual(0,process.returncode,process.stderr)
            self.assertEqual(task['task_id'],json.loads(process.stdout)['task_id'])

    def event(self,runtime,name,**extra):return runtime.hook({'hook_event_name':name,'session_id':'session',**extra})

    def test_failed_acceptance_continues_same_session_and_pass_closes_actual_control_task(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,review,task=self.setup_task(Path(temp))
            self.event(runtime,'PreToolUse',tool_use_id='edit1',tool_name='apply_patch',tool_input={'command':'edit out.json'})
            (w/'out.json').write_text('{"total":41}')
            self.event(runtime,'PostToolUse',tool_use_id='edit1',tool_name='apply_patch',tool_response={'success':True})
            self.assertEqual('block',self.event(runtime,'Stop')['decision'])
            self.assertEqual(task['task_id'],runtime.status('session')['task_id'])
            (w/'out.json').write_text('{"total":42}')
            response=self.event(runtime,'Stop',stop_hook_active=True)
            self.assertNotIn('decision',response)
            state=runtime.status('session');self.assertEqual('COMPLETED',state['status']);self.assertTrue(state['hook_observed'])
            receipt=json.loads(Path(state['last_receipt']).read_text(encoding='utf-8'))
            self.assertEqual('COMPLETED',receipt['control_state']['status']);self.assertEqual(1,state['user_messages'])
            self.assertEqual(['FAIL','CHALLENGE_PASS'],[r['status'] for r in state['reviews']])
            self.assertEqual(1,len(runtime.memory(w).search('exact sum')))
            (w/'out.json').write_text('{"total":43}')
            self.event(runtime,'Stop');self.assertEqual('UNVERIFIED',runtime.status('session')['status'])
            self.assertEqual('COMPLETED',json.loads(Path(state['last_receipt']).read_text(encoding='utf-8'))['status'])

    def test_repeated_bad_candidate_has_bounded_feedback_and_never_seeds_success_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,_,_=self.setup_task(Path(temp),repairs=1);(w/'out.json').write_text('{"total":41}')
            first=self.event(runtime,'Stop');second=self.event(runtime,'Stop',stop_hook_active=True);third=self.event(runtime,'Stop',stop_hook_active=True)
            self.assertEqual('block',first['decision']);self.assertEqual('block',second['decision']);self.assertNotIn('decision',third)
            state=runtime.status('session');self.assertEqual('UNVERIFIED',state['status']);self.assertEqual(1,len(state['reviews']))
            self.assertEqual([],runtime.memory(w).search('exact sum'))

    def test_interrupt_with_pending_call_stays_unknown_and_new_goal_cannot_replace_it(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,review,_=self.setup_task(Path(temp))
            self.event(runtime,'PreToolUse',tool_use_id='pending',tool_name='Bash',tool_input={'command':'external operation'})
            self.event(runtime,'Interrupt')
            self.assertEqual('NEEDS_RECONCILIATION',runtime.status('session')['status'])
            self.assertEqual('NEEDS_RECONCILIATION',runtime.verify('session')['status'])
            with self.assertRaises(ValueError):runtime.begin('session',w,['source.json'],['new.json'],review)

    def test_hook_installation_is_idempotent_preserves_other_hooks_and_requires_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);codex=root/'codex';codex.mkdir();target=codex/'hooks.json'
            other={'hooks':[{'type':'command','command':'existing-host-hook'}]};target.write_text(json.dumps({'hooks':{'Stop':[other]}}))
            for _ in range(2):result=install_hooks(codex,root/'native')
            config=json.loads(target.read_text(encoding='utf-8'));self.assertIn(other,config['hooks']['Stop'])
            self.assertEqual(2,len(config['hooks']['Stop']));self.assertEqual('CONFIGURED_REQUIRES_HOST_TRUST',result['status'])

    def test_async_tool_output_does_not_pretend_its_process_has_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,_,_=self.setup_task(Path(temp));(w/'out.json').write_text('{"total":42}')
            self.event(runtime,'PreToolUse',tool_use_id='long',tool_name='Bash',tool_input={'command':'long operation'})
            self.event(runtime,'PostToolUse',tool_use_id='long',tool_name='Bash',tool_response={'session_id':12,'exit_code':None})
            state=runtime.verify('session',at_stop=True)
            self.assertEqual('NEEDS_RECONCILIATION',state['status'])
            self.assertEqual(['long'],state['control_state']['unknown_tool_calls'])

    def test_verification_inside_a_tool_defers_until_stop_instead_of_waiting_on_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,_,_=self.setup_task(Path(temp));(w/'out.json').write_text('{"total":42}')
            self.event(runtime,'PreToolUse',tool_use_id='verify-command',tool_name='Bash',tool_input={'command':'native_task.py verify'})
            self.assertEqual('VERIFICATION_QUEUED',runtime.verify('session')['status'])
            self.assertEqual('RUNNING',runtime.status('session')['status'])
            self.event(runtime,'PostToolUse',tool_use_id='verify-command',tool_name='Bash',tool_response={'exit_code':0})
            self.assertNotIn('decision',self.event(runtime,'Stop'))
            self.assertEqual('COMPLETED',runtime.status('session')['status'])

    def test_existing_project_test_is_frozen_and_cannot_be_changed_to_fit_bad_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);w=root/'workspace';w.mkdir();(w/'source.json').write_text('{"value":42}')
            reviewer=w/'test_existing.py';reviewer.write_text('import json,unittest\nfrom pathlib import Path\nclass Goal(unittest.TestCase):\n def test_value(self):self.assertEqual(42,json.loads(Path("out.json").read_text())["total"])\n')
            runtime=NativeSession(root/'native')
            with self.assertRaises(ValueError):runtime.begin('project-tests',w,['source.json'],['out.json'],reviewer,goal='use project checks')
            runtime.begin('project-tests',w,['source.json','test_existing.py'],['out.json'],reviewer,goal='use project checks')
            (w/'out.json').write_text('{"total":41}');reviewer.write_text('import unittest\nclass Fake(unittest.TestCase):\n def test_pass(self):pass\n')
            result=runtime.verify('project-tests')
            self.assertNotEqual('COMPLETED',result['status']);self.assertIn('read-only input changed',result['reason'])

    def test_unmatched_pre_hook_is_recorded_as_partial_coverage_not_a_running_process(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,_,_=self.setup_task(Path(temp));(w/'out.json').write_text('{"total":42}')
            self.event(runtime,'PreToolUse',tool_use_id='intent-only',tool_name='Bash',tool_input={'command':'a host-rejected command'})
            self.assertNotIn('decision',self.event(runtime,'Stop'))
            state=runtime.status('session');self.assertEqual('COMPLETED',state['status'])
            self.assertEqual('partial',state['tool_observation_coverage'])
            self.assertIn('intent-only',state['unmatched_intents'])
            self.assertEqual({},state['pending'])

    def test_next_user_goal_cannot_inherit_previous_tasks_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w,_,_=self.setup_task(Path(temp));(w/'out.json').write_text('{"total":42}')
            self.assertEqual('COMPLETED',runtime.verify('session')['status'])
            self.event(runtime,'UserPromptSubmit',cwd=str(w),prompt='$agi-like please deliver a different file')
            self.assertEqual('NOT_REGISTERED',runtime.status('session')['status'])
            self.event(runtime,'PostToolUse',tool_name='apply_patch',tool_input={'command':'edit new file'},tool_response={'ok':True})
            reminder=self.event(runtime,'Stop');self.assertEqual('block',reminder['decision'])
            self.event(runtime,'UserPromptSubmit',cwd=str(w),prompt=reminder['reason'])
            with runtime.db() as db:
                original=json.loads(db.execute('SELECT value FROM prompts WHERE id=?',('session',)).fetchone()[0])
            self.assertEqual('$agi-like please deliver a different file',original['goal'])
            self.assertNotIn('decision',self.event(runtime,'Stop'))
