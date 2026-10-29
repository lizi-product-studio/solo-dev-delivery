"""Public managed-task lifecycle with labelled local model/process doubles."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/'portable-agent-kit/scripts/agent_task.py'


class ManagedTaskCliTests(unittest.TestCase):
    def prepare(self, root, *, slow=False, unknown_write=False):
        workspace=root/'workspace';workspace.mkdir()
        (workspace/'TASK.md').write_text('Sum the original numbers into report.json.',encoding='utf-8')
        (workspace/'numbers.json').write_text('{"values":[20,22]}',encoding='utf-8')
        plan={'inputs':['TASK.md','numbers.json'],'outputs':['report.json'],
              'steps':['read original files','write the actual sum'],'unknowns':[]}
        (root/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        (root/'review.py').write_text('import unittest,json\nfrom pathlib import Path\n'
            'class Check(unittest.TestCase):\n def test_total(self):\n'
            '  self.assertEqual(sum(json.loads(Path("numbers.json").read_text())["values"]),'
            'json.loads(Path("report.json").read_text())["total"])\n',encoding='utf-8')
        model=root/'model.py'
        model.write_text('''import json,sys,time
from pathlib import Path
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
r=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
with Path(CALLS).open('a',encoding='utf-8') as out:out.write(str(r['step'])+'\\n')
if SLOW and r['step']==2:
 Path(READY).write_text('model is pending')
 time.sleep(1.5)
reads={e['decision'].get('arguments',{}).get('path') for e in r['observations'] if e['action']=='tool' and e['decision']['name'].startswith('read_')}
if 'TASK.md' not in reads:d={'action':'tool','name':'read_file','arguments':{'path':'TASK.md'}}
elif 'numbers.json' not in reads:d={'action':'tool','name':'read_json','arguments':{'path':'numbers.json'}}
else:d={'action':'tool','name':'write_json','arguments':{'path':'report.json','value':{'total':42}}}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}}))
print(json.dumps({'type':'turn.completed'}))
'''.replace('CALLS',repr(str(root/'model-calls.txt'))).replace('READY',repr(str(root/'model-pending.txt')))
            .replace('SLOW',repr(slow)),encoding='utf-8')
        env={**os.environ,'CODEX_CLI_PATH':str(model),'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
        if unknown_write:
            inject=root/'host-fault';inject.mkdir()
            (inject/'sitecustomize.py').write_text('''from pathlib import Path
original=Path.write_bytes
def write(self,data):
 result=original(self,data)
 if self.name=='report.json' and self.parent.name=='workspace':
  raise TimeoutError('simulated return loss after real file effect')
 return result
Path.write_bytes=write
''',encoding='utf-8')
            env['PYTHONPATH']=str(inject)
        argv=['--goal','Sum the original numbers into report.json.','--workspace',str(workspace),
              '--allow-write','report.json','--authorization-ref','local-lifecycle-fixture',
              '--control-root',str(root/'control'),'--project-store',str(root/'learning'),
              '--plan',str(root/'plan.json'),'--review-script',str(root/'review.py'),'--max-steps','7']
        return argv,env

    def invoke(self, root, arguments, env):
        proc=subprocess.run([sys.executable,'-X','utf8',str(CLI),*arguments],env=env,
                            capture_output=True,encoding='utf-8',timeout=30)
        self.assertTrue(proc.stdout,proc.stderr)
        return proc,json.loads(proc.stdout)

    def operation(self,root,operation,env,*extra):
        return self.invoke(root,[operation,'--control-root',str(root/'control'),*extra],env)

    def test_pause_resume_uses_same_task_budget_and_preserves_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);argv,env=self.prepare(root)
            argv[argv.index('--max-steps')+1]='3'
            _,started=self.invoke(root,['start',*argv,'--steps-per-run','1'],env)
            self.assertEqual('PAUSED',started['status'])
            manifest=(root/'control/managed-task.json').read_bytes()
            before=hashlib.sha256((root/'control/control.sqlite3').read_bytes()).hexdigest()
            _,state=self.operation(root,'status',env)
            self.assertTrue(state['can_resume'])
            self.assertEqual(before,hashlib.sha256((root/'control/control.sqlite3').read_bytes()).hexdigest())
            _,again=self.operation(root,'resume',env,'--steps-per-run','1')
            self.assertEqual('PAUSED',again['status'])
            _,done=self.operation(root,'resume',env)
            self.assertEqual('COMPLETED',done['status'])
            self.assertEqual(started['task_id'],done['task_id'])
            self.assertEqual(3,done['execution_steps'])
            self.assertEqual('1\n2\n3\n',(root/'model-calls.txt').read_text())
            self.assertEqual(manifest,(root/'control/managed-task.json').read_bytes())
            self.assertEqual(2,len(list((root/'control/operations').glob('*/previous-runtime.json'))))
            _,blocked=self.operation(root,'resume',env)
            self.assertEqual('OPERATION_REJECTED',blocked['status'])

    def test_cancel_paused_task_acknowledges_without_any_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);argv,env=self.prepare(root)
            self.invoke(root,[*argv,'--steps-per-run','1'],env)
            _,result=self.operation(root,'cancel',env,'--reason','user cancelled','--request-id','cancel-once')
            self.assertEqual('CANCELLED',result['status'])
            self.assertEqual('1\n',(root/'model-calls.txt').read_text())
            _,state=self.operation(root,'status',env)
            self.assertEqual('CANCELLED',state['status'])
            self.assertEqual('ACKNOWLEDGED',state['control_requests'][0]['status'])
            self.assertFalse((root/'workspace/report.json').exists())

    def test_live_pause_and_cancel_wait_for_model_return_then_discard_action(self):
        for operation in ('pause','cancel'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);argv,env=self.prepare(root,slow=True)
                proc=subprocess.Popen([sys.executable,'-X','utf8',str(CLI),*argv],env=env,
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf-8')
                try:
                    deadline=time.monotonic()+10
                    while not (root/'model-pending.txt').exists():
                        if proc.poll() is not None:self.fail('worker exited before live request: '+proc.communicate()[1])
                        if time.monotonic()>deadline:self.fail('model did not reach pending fixture')
                        time.sleep(0.02)
                    _,accepted=self.operation(root,operation,env,'--reason','live control fixture')
                    self.assertEqual(operation.upper()+'_REQUESTED',accepted['status'])
                    stdout,stderr=proc.communicate(timeout=15)
                    ended=json.loads(stdout)
                    self.assertEqual('PAUSED' if operation=='pause' else 'CANCELLED',ended['status'],stderr)
                    self.assertFalse((root/'workspace/report.json').exists())
                    self.assertEqual('1\n2\n',(root/'model-calls.txt').read_text())
                    _,state=self.operation(root,'status',env)
                    self.assertEqual('ACKNOWLEDGED',state['control_requests'][0]['status'])
                    if operation=='pause':
                        _,done=self.operation(root,'resume',env)
                        self.assertEqual('COMPLETED',done['status'])
                        self.assertEqual('1\n2\n3\n4\n',(root/'model-calls.txt').read_text())
                finally:
                    if proc.poll() is None:proc.kill();proc.communicate()

    def test_real_write_with_lost_return_is_read_back_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);argv,env=self.prepare(root,unknown_write=True)
            _,result=self.invoke(root,argv,env)
            self.assertEqual('NEEDS_RECONCILIATION',result['status'])
            _,state=self.operation(root,'status',env)
            action_id=state['pending']['id']
            _,blocked=self.operation(root,'resume',env)
            self.assertEqual('OPERATION_REJECTED',blocked['status'])
            # Reconciliation does not execute model code or write the output.
            _,reconciled=self.operation(root,'reconcile',env,'--action-id',action_id)
            self.assertEqual('PAUSED',reconciled['observed']['status'])
            self.assertEqual('1\n2\n3\n',(root/'model-calls.txt').read_text())
            safe={**env};safe.pop('PYTHONPATH',None)
            # A finishing model sees the reconciled successful observation.
            model=root/'model.py'
            source=model.read_text(encoding='utf-8')
            source=source.replace("else:d={'action':'tool','name':'write_json','arguments':{'path':'report.json','value':{'total':42}}}",
                                  "else:d={'action':'finish','answer':'verified persisted total'}")
            model.write_text(source,encoding='utf-8')
            _,done=self.operation(root,'resume',safe)
            self.assertEqual('COMPLETED',done['status'])
            self.assertEqual('1\n2\n3\n4\n',(root/'model-calls.txt').read_text())
            self.assertEqual({'total':42},json.loads((root/'workspace/report.json').read_text()))

    def test_cancel_during_unknown_waits_for_readback_then_acknowledges_without_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);argv,env=self.prepare(root,unknown_write=True)
            self.invoke(root,argv,env)
            _,before=self.operation(root,'status',env)
            action_id=before['pending']['id']
            _,waiting=self.operation(root,'cancel',env,'--reason','stop after outcome is known')
            self.assertEqual('CANCEL_REQUESTED',waiting['status'])
            self.assertEqual('UNKNOWN',waiting['observed']['status'])
            _,cancelled=self.operation(root,'reconcile',env,'--action-id',action_id)
            self.assertEqual('CANCELLED',cancelled['status'])
            self.assertEqual('1\n2\n3\n',(root/'model-calls.txt').read_text())
            _,reused=self.operation(root,'reconcile',env,'--action-id',action_id)
            self.assertTrue(reused['reused'])
            self.assertEqual('CANCELLED',reused['observed']['status'])


if __name__=='__main__':unittest.main()
