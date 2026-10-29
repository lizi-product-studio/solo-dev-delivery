import json, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from control_plane.runtime import DeliveryRuntime


class RuntimeEventCliTests(unittest.TestCase):
    def test_host_event_cli_writes_to_same_persisted_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); db=root/'control.sqlite3'
            contract={"task_id":"event-task","goal":"g","owner":"host","candidate":"c","authorization_ref":"local","scope":["x"],"excluded":["external"],"policy":{"workspace_write":False},"acceptance":[{"id":"A","expected":"e","environment":"isolated"}]}
            rt=DeliveryRuntime(db)
            state=rt.create_task(contract,{"evidence.hash":True,"checkpoint.persist":True})
            lease=rt.claim(state['task_id'],'host')
            script=Path(__file__).resolve().parents[1]/'portable-agent-kit/scripts/runtime_event.py'
            def event(op,payload):
                p=subprocess.run([sys.executable,str(script),'--db',str(db),'--task-id',state['task_id'],'--lease-id',lease['lease_id'],'--op',op,'--payload',json.dumps(payload)],capture_output=True,text=True,check=True)
                return json.loads(p.stdout)
            event('start',{'executor':'host'})
            result=event('tool_result',{'tool_call_id':'call-1','status':'SUCCEEDED'})
            self.assertEqual('RUNNING',result['state']['status'])
            self.assertEqual(3,len(rt.events(state['task_id'])))


if __name__ == '__main__': unittest.main()
