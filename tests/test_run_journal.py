import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

KIT=Path(__file__).resolve().parents[1]/'portable-agent-kit'
sys.path.insert(0,str(KIT))
from agent_runtime.journal import RunJournal,JournalError
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.tools import ToolError


class RunJournalTests(unittest.TestCase):
    def test_safe_pause_reuses_observations_and_same_total_step_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=ToolRegistry();calls=[]
            tools.register('inspect',lambda: calls.append(1) or {'value':42})
            memory=MemoryStore(root/'m.sqlite3');journal=RunJournal(root/'state.sqlite3')
            first=AgentRuntime(lambda _: {'action':'tool','name':'inspect','arguments':{}},tools,memory,max_steps=3)
            paused=first.run('g',task_id='t',journal=journal,steps_per_run=1)
            self.assertEqual('PAUSED',paused.status)
            def model(req):
                self.assertEqual(2,req['step'])
                self.assertEqual({'value':42},req['observations'][0]['result'])
                return {'action':'finish','answer':'42'}
            done=AgentRuntime(model,tools,memory,max_steps=3).run('g',task_id='t',journal=RunJournal(root/'state.sqlite3'),resume=True,verifier=lambda *a:True)
            self.assertEqual('COMPLETED',done.status)
            self.assertEqual([1],calls)
            with self.assertRaises(ValueError):
                first.run('g',task_id='t',journal=journal,resume=True)

    def test_stale_binding_and_concurrent_checkpoint_updates_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store=RunJournal(Path(temp)/'j.sqlite3')
            saved=store.create('t',{'goal':'a'},{'phase':'READY'})
            with self.assertRaisesRegex(JournalError,'STALE'):
                store.check_binding('t',{'goal':'b'})
            store.save('t',saved['generation'],{'phase':'PAUSED'})
            with self.assertRaisesRegex(JournalError,'CONCURRENT'):
                store.save('t',saved['generation'],{'phase':'READY'})

    def test_return_timeout_after_effect_blocks_loop_and_resume_until_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tools=ToolRegistry();counter=root/'counter.txt'
            def append():
                with counter.open('a') as stream:stream.write('x')
                raise TimeoutError('effect happened but return was lost')
            tools.register('append',append)
            model=lambda req:{'action':'tool','name':'append','arguments':{}}
            journal=RunJournal(root/'j.sqlite3');memory=MemoryStore(root/'m.sqlite3')
            runtime=AgentRuntime(model,tools,memory)
            first=runtime.run('append once',task_id='t',journal=journal,steps_per_run=1)
            self.assertEqual('NEEDS_RECONCILIATION',first.status)
            second=runtime.run('append once',task_id='t',journal=journal,resume=True,steps_per_run=1)
            self.assertEqual('NEEDS_RECONCILIATION',second.status)
            self.assertEqual('x',counter.read_text())

    def test_process_crash_after_side_effect_requires_readback_not_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker=root/'worker.py'
            worker.write_text('''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_runtime import AgentRuntime,MemoryStore,ToolRegistry
from agent_runtime.journal import RunJournal
root=Path(sys.argv[2]);mode=sys.argv[3];tools=ToolRegistry()
def append():
    with (root/'counter.txt').open('a') as f:f.write('x')
    if mode=='crash':os._exit(23)
    return {'wrote':True}
tools.register('append',append)
def model(req):
    if req['step']==1:return {'action':'tool','name':'append','arguments':{}}
    return {'action':'finish','answer':'one write'}
result=AgentRuntime(model,tools,MemoryStore(root/'m.sqlite3')).run('append once',task_id='t',journal=RunJournal(root/'state.sqlite3'),resume=mode!='crash',verifier=lambda *a:(root/'counter.txt').read_text()=='x')
print(result.status)
''',encoding='utf-8')
            def invoke(mode):return subprocess.run([sys.executable,str(worker),str(KIT),str(root),mode],capture_output=True,encoding='utf-8',timeout=10)
            self.assertEqual(23,invoke('crash').returncode)
            self.assertIn('NEEDS_RECONCILIATION',invoke('resume').stdout)
            self.assertEqual('x',(root/'counter.txt').read_text())
            journal=RunJournal(root/'state.sqlite3');pending=journal.load('t')['snapshot']['pending']
            with self.assertRaisesRegex(JournalError,'READBACK_NOT_VERIFIED'):
                journal.reconcile('t',action_id=pending['id'],observation={'wrote':True},verify_readback=lambda *a:False)
            journal.reconcile('t',action_id=pending['id'],observation={'wrote':True},verify_readback=lambda *a:(root/'counter.txt').read_text()=='x')
            resumed=invoke('resume')
            self.assertEqual(0,resumed.returncode,resumed.stderr)
            self.assertIn('COMPLETED',resumed.stdout)
            self.assertEqual('x',(root/'counter.txt').read_text())

    def test_explicit_unknown_and_partially_executed_skill_never_auto_retry(self):
        for mode in ('tool','skill'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);calls=[];tools=ToolRegistry()
                def operation():
                    calls.append(1)
                    if mode=='skill':raise ToolError('second skill step refused after first wrote')
                    return {'status':'UNKNOWN'}
                tools.register('operation',operation)
                skills={'operation':lambda arguments:operation()}
                runtime=AgentRuntime(lambda req:{'action':mode,'name':'operation','arguments':{}},tools,
                                     MemoryStore(root/'m.sqlite3'),skills)
                journal=RunJournal(root/'j.sqlite3')
                result=runtime.run('one effect',task_id='t',journal=journal,steps_per_run=1)
                self.assertEqual('NEEDS_RECONCILIATION',result.status)
                result=runtime.run('one effect',task_id='t',journal=journal,resume=True)
                self.assertEqual('NEEDS_RECONCILIATION',result.status)
                self.assertEqual([1],calls)


if __name__=='__main__':unittest.main()
