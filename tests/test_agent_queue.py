import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit/scripts'))
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.scheduler import TaskScheduler
import agent_queue


class AgentQueueIntegrationTests(unittest.TestCase):
    def test_bounded_worker_finishes_dependent_contract_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            fake=root/'model.py'
            fake.write_text('''import json,sys
request=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
if request['step']==1:
    value={'action':'tool','name':'write_file','arguments':{'path':'result.json','text':'{"value":7}'}}
else:value={'action':'finish','answer':'done'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(value)}}))
print(json.dumps({'type':'turn.completed'}))
''',encoding='utf-8')
            contract={'owner':'agent-runtime','candidate':'v1','authorization_ref':'fixture',
                'scope':['result.json'],'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
                'acceptance':[{'id':'value','expected':'value=7','environment':'local',
                    'assertion':{'type':'json_pointer_equals','path':'result.json','pointer':'/value','value':7}}]}
            path=root/'contract.json';path.write_text(json.dumps(contract),encoding='utf-8')
            queue=TaskScheduler(root/'queue.sqlite3')
            job={'goal':'write result','workspace':str(workspace),'contract':str(path)}
            queue.enqueue('first',job);queue.enqueue('second',job,['first'])
            proc=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/agent_queue.py'),
                '--queue',str(root/'queue.sqlite3'),'work','--worker','test-worker','--control-root',str(root/'control'),
                '--max-tasks','2'],env={**os.environ,'CODEX_CLI_PATH':str(fake)},capture_output=True,encoding='utf-8',timeout=20)
            self.assertEqual(0,proc.returncode,proc.stderr)
            self.assertEqual(['COMPLETED','COMPLETED'],[row['status'] for row in queue.list_tasks()])
            for task in queue.list_tasks():
                report=json.loads(Path(task['result']['receipt']).read_text(encoding='utf-8'))
                self.assertEqual('COMPLETED',report['control_state']['status'])

    def test_public_cli_preserves_model_review_and_auto_verify_options_across_cwds(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            (workspace/'TASK.md').write_text('Produce result.json with value=7.',encoding='utf-8')
            fake=root/'model.py'
            fake.write_text('''import json,sys
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
assert '--ignore-user-config' in sys.argv
assert sys.argv[sys.argv.index('-m')+1]=='queue-test-model'
assert 'model_reasoning_effort="medium"' in sys.argv
request=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
assert request['budget']['max_steps']==3
if request['step']==1:
 value={'action':'tool','name':'read_file','arguments':{'path':'TASK.md'}}
elif request['step']==2:
 value={'action':'tool','name':'write_json','arguments':{'path':'result.json','value':{'value':7}}}
else:raise AssertionError('auto-verify was not forwarded; an extra model call was made')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(value)}}))
print(json.dumps({'type':'turn.completed'}))
''',encoding='utf-8')
            review=root/'review.py'
            review.write_text('import json,unittest\nfrom pathlib import Path\n'
                'class Result(unittest.TestCase):\n'
                ' def test_value(self):self.assertEqual(7,json.loads(Path("result.json").read_text())["value"])\n',
                encoding='utf-8')
            review_hash=hashlib.sha256(review.read_bytes()).hexdigest()
            contract={'owner':'runtime','candidate':'queue-options','authorization_ref':'fixture',
                'scope':['result.json'],'inputs':['TASK.md'],'excluded':['external'],
                'required_review_sha256':review_hash,
                'policy':{'workspace_write':True,'shell_required':False,'require_input_reads':True},
                'acceptance':[{'id':'result','expected':'correct result','environment':'local',
                    'assertion':{'type':'file_exists','path':'result.json'}}]}
            (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
            job={'goal':'produce verified result','workspace':'workspace','contract':'contract.json',
                 'provider':'codex','model':'queue-test-model','max_steps':3,'project_id':'queue-project',
                 'effort':'medium','isolate_model_config':True,'review_script':'review.py','auto_verify':True}
            (root/'job.json').write_text(json.dumps(job),encoding='utf-8')
            entry=str(ROOT/'portable-agent-kit/scripts/agent_queue.py')
            queued=subprocess.run([sys.executable,entry,'--queue',str(root/'queue.sqlite3'),
                'enqueue','--task-id','configured','--job',str(root/'job.json')],
                cwd=root,capture_output=True,encoding='utf-8',timeout=10)
            self.assertEqual(0,queued.returncode,queued.stderr)
            queue=TaskScheduler(root/'queue.sqlite3')
            saved=queue.get('configured')['payload']
            self.assertEqual(str(review.resolve()),saved['review_script'])
            self.assertEqual(str(workspace.resolve()),saved['workspace'])
            other=root/'other-cwd';other.mkdir()
            worked=subprocess.run([sys.executable,entry,'--queue',str(root/'queue.sqlite3'),
                'work','--worker','configured-worker','--control-root',str(root/'control')],
                cwd=other,env={**os.environ,'CODEX_CLI_PATH':str(fake)},
                capture_output=True,encoding='utf-8',timeout=20)
            self.assertEqual(0,worked.returncode,worked.stderr)
            task=queue.get('configured')
            self.assertEqual('COMPLETED',task['status'],task.get('result'))
            report=json.loads(Path(task['result']['receipt']).read_text(encoding='utf-8'))
            self.assertEqual('COMPLETED',report['status'])
            self.assertEqual(2,report['steps'])
            self.assertEqual('queue-project',report['memory_scope'])
            control=Path(task['result']['receipt']).parent
            frozen=control/'trusted-review.py'
            self.assertEqual(review.read_bytes(),frozen.read_bytes())
            approved=json.loads((control/'approved-contract.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(),approved['required_review_sha256'])
            self.assertFalse(frozen.resolve().is_relative_to(workspace.resolve()))
            raw_review=json.loads(Path(report['independent_reviews'][0]['receipt']).read_text(encoding='utf-8'))
            self.assertEqual(str(frozen.resolve()),raw_review['test'])

    def test_invalid_job_options_are_rejected_instead_of_silently_ignored(self):
        base={'goal':'g','workspace':'work','contract':'contract.json'}
        invalid=[{'effort':'unsupported'}, {'effort':None}, {'effort':True},
                 {'provider':'api','effort':'medium'},
                 {'provider':'api','model':'m','isolate_model_config':True},
                 {'isolate_model_config':True}, {'isolate_model_config':'true'},
                 {'isolate_model_config':1}, {'auto_verify':1}, {'auto_verify':'false'},
                 {'auto_verify':True}, {'review_script':''}, {'review_script':False},
                 {'model':[]}, {'model':' '}, {'project_id':None}, {'provider':[]},
                 {'max_steps':True}, {'unrecognized_option':'discard me'}]
        for overrides in invalid:
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                agent_queue.validate_job({**base,**overrides})
        valid={**base,'provider':'api','model':'m','isolate_model_config':False,'auto_verify':False}
        self.assertEqual(valid,agent_queue.validate_job(valid))

    def test_worker_freezes_review_and_adds_a_required_hash_when_unbound(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            review=root/'review.py';review.write_bytes(b'# trusted review version 1\n')
            contract=root/'contract.json';contract.write_text('{}',encoding='utf-8')
            initial=review.read_bytes();received=[]
            def entry(argv):
                received.extend(argv)
                frozen=Path(argv[argv.index('--review-script')+1])
                approved=Path(argv[argv.index('--contract')+1])
                self.assertEqual(initial,frozen.read_bytes())
                self.assertEqual(hashlib.sha256(initial).hexdigest(),
                                 json.loads(approved.read_text())['required_review_sha256'])
                review.write_bytes(b'# source changed after worker froze it\n')
                self.assertEqual(initial,frozen.read_bytes())
                Path(argv[argv.index('--receipt')+1]).write_text(
                    json.dumps({'status':'COMPLETED','task_id':'freeze'}),encoding='utf-8')
                return 0
            with patch.object(agent_queue,'run_agent',entry):
                result=agent_queue.worker_handler(root/'control')({'task_id':'freeze','payload':{
                    'goal':'g','workspace':str(workspace),'contract':str(contract),
                    'review_script':str(review)}})
            self.assertEqual('COMPLETED',result['status'])
            self.assertIn('--review-script',received)

    def test_worker_rejects_changed_missing_or_untrusted_reviewer_before_execution(self):
        for mode in ('changed-binding','missing-review','review-in-workspace','control-in-workspace'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);workspace=root/'workspace';workspace.mkdir()
                review=(workspace if mode=='review-in-workspace' else root)/'review.py'
                review.write_bytes(b'# trusted review\n')
                contract=root/'contract.json'
                value={'required_review_sha256':'0'*64} if mode in ('changed-binding','missing-review') else {}
                contract.write_text(json.dumps(value),encoding='utf-8')
                job={'goal':'g','workspace':str(workspace),'contract':str(contract)}
                if mode!='missing-review':job['review_script']=str(review)
                control=(workspace if mode=='control-in-workspace' else root)/'control'
                with patch.object(agent_queue,'run_agent') as entry,self.assertRaises(ValueError):
                    agent_queue.worker_handler(control)({'task_id':'invalid','payload':job})
                entry.assert_not_called()


if __name__=='__main__':unittest.main()
