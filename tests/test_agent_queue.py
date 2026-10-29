import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.scheduler import TaskScheduler


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


if __name__=='__main__':unittest.main()
