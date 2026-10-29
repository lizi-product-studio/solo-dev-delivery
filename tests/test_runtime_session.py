import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable-agent-kit"))
from agent_runtime import RuntimeSession
from control_plane.runtime import DeliveryRuntime


class RuntimeSessionTests(unittest.TestCase):
    def test_pause_resume_and_cancel_use_one_host_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=DeliveryRuntime(Path(temp)/'control.sqlite3')
            contract={"task_id":"s","goal":"g","owner":"a","candidate":"c","authorization_ref":"local","scope":["x"],"excluded":["external"],"policy":{"workspace_write":False},"acceptance":[{"id":"A","expected":"e","environment":"isolated"}]}
            state=runtime.create_task(contract,{"evidence.hash":True,"checkpoint.persist":True})
            lease=runtime.claim(state['task_id'],'host')
            session=RuntimeSession(runtime,state['task_id'],lease['lease_id'])
            session.start(); paused=session.pause(checkpoint_id='cp',resume_token='token',cursor='one')
            self.assertEqual('PAUSED',paused[0]['status'])
            resumed=session.resume('token')
            self.assertEqual('UNKNOWN' if resumed[0].get('unknown_tool_calls') else 'RUNNING',resumed[0]['status'])
            cancelled=session.cancel('test stop')
            self.assertEqual('CANCELLED',cancelled[0]['status'])


if __name__ == '__main__': unittest.main()
