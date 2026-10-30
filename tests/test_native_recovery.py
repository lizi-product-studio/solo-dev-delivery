"""Recovery inspection must work while effects remain fenced, including old tasks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.native_session import NativeSession
from agent_runtime.native_recovery import recovery_call


class NativeRecoveryTests(unittest.TestCase):
    def task(self,root):
        workspace=root/'workspace';workspace.mkdir();(workspace/'source.txt').write_text('source',encoding='utf-8')
        review=root/'review.py';review.write_text('import unittest\nclass Goal(unittest.TestCase):\n def test_present(self):self.assertTrue(True)\n',encoding='utf-8')
        runtime=NativeSession(root/'native')
        runtime.begin('session',workspace,['source.txt'],['out.txt'],review,goal='deliver out.txt')
        runtime.hook({'hook_event_name':'PreToolUse','session_id':'session','tool_use_id':'old-write','tool_name':'Bash','tool_input':{'command':'unknown external write'}})
        runtime.hook({'hook_event_name':'Interrupt','session_id':'session'})
        return runtime,workspace

    def event(self,runtime,event,tool,arguments):
        return runtime.hook({'hook_event_name':event,'session_id':'session','tool_use_id':'recovery-read','tool_name':tool,'tool_input':arguments,'tool_response':{'exit_code':0}})

    def test_legacy_unknown_task_allows_inspection_and_keeps_write_fence(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w=self.task(Path(temp));before=runtime.status('session')
            for tool,args in [('Read',{'file_path':str(w/'source.txt')}),('Bash',{'command':"Get-Content -LiteralPath '"+str(w/'source.txt')+"'"}),('mcp__agi_like_runtime__native_status',{'session_id':'session'})]:
                result=self.event(runtime,'PreToolUse',tool,args)
                self.assertNotIn('permissionDecision',result.get('hookSpecificOutput',{}))
                self.event(runtime,'PostToolUse',tool,args)
            denied=self.event(runtime,'PreToolUse','apply_patch',{'patch':'change output'})
            self.assertEqual('deny',denied['hookSpecificOutput']['permissionDecision'])
            self.assertIn('readback',denied['hookSpecificOutput']['permissionDecisionReason'])
            after=runtime.status('session')
            for key in ('pending','status','created','tool_count','repairs','lease_id'):
                self.assertEqual(before[key],after[key],key)
            report=runtime.readback('session');self.assertEqual('source',report['artifacts'][0]['preview'])
            self.assertIn('error',report['artifacts'][1])
            self.assertEqual(['old-write'],runtime.recover('session')['recovery']['unresolved_tool_calls'])
            self.assertEqual(['old-write'],runtime.control(after).get(after['task_id'])['unknown_tool_calls'])

    def test_reads_never_consume_execution_budget_or_become_pending_on_interrupt(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w=self.task(Path(temp))
            for status in ('NEEDS_RECONCILIATION','BUDGET_EXHAUSTED','UNVERIFIED','RUNNING'):
                with runtime.db() as db:
                    task=runtime.load(db,'session');task['status']=status;task['tool_count']=task['max_tools'];task['created']=time.time()-task['max_seconds']-1
                    runtime.store(db,'session',task)
                result=self.event(runtime,'PreToolUse','Bash',{'command':'Get-Location'})
                self.assertNotIn('permissionDecision',result['hookSpecificOutput'])
                after=runtime.status('session');self.assertEqual({},after['intents']);self.assertEqual(['old-write'],list(after['pending']))
                self.assertEqual(status,after['status'])

    def test_classifier_rejects_execution_disguised_as_inspection(self):
        script=ROOT/'portable-agent-kit/scripts/native_task.py'
        def allows(tool,args):return recovery_call(tool,args,session_id='session',home=ROOT,script=script)
        for command in ['Get-Content x; Remove-Item x','Get-Content $(Set-Content x y)',
                        'Get-Content x | Set-Content y','Get-Content x > y','Get-Content `x',
                        'python -c "print(42)"','rg --pre malicious x',
                        'Get-Content x\nSet-Content y z','& evil.exe status']:
            self.assertFalse(allows('Bash',{'command':command}),command)
        self.assertFalse(allows('mcp__agi_like_runtime__native_status',{'session_id':'other'}))
        self.assertFalse(allows('other_server.native_status',{'session_id':'session'}))
        command='& "'+sys.executable+'" "'+str(script)+'" --home "'+str(ROOT.resolve())+'" readback --session-id session'
        self.assertTrue(allows('Bash',{'command':command}))
        self.assertFalse(allows('Bash',{'command':command+' --output overwrite'}))
        self.assertFalse(allows('Bash',{'command':command.replace('readback','begin')}))
        self.assertFalse(allows('Bash',{'command':command.replace('native_task.py','untrusted.py')}))
        self.assertTrue(allows('Bash',{'command':['pwsh.exe','-NoProfile','-Command',"Get-Content -LiteralPath 'a b.txt'"]}))
        self.assertFalse(allows('Bash',{'command':['pwsh.exe','-Command','Get-Content x; Set-Content y z']}))

    def test_cli_recovery_is_available_for_an_existing_blocked_session(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime,w=self.task(Path(temp))
            for operation in ('readback','recover'):
                process=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/native_task.py'),'--home',str(runtime.home),operation,'--session-id','session'],capture_output=True,encoding='utf-8',timeout=10)
                self.assertEqual(0,process.returncode,process.stderr)
                report=json.loads(process.stdout);self.assertEqual('NEEDS_RECONCILIATION',report['status'])
            self.assertEqual(['old-write'],list(runtime.status('session')['pending']))


if __name__=='__main__':unittest.main()
