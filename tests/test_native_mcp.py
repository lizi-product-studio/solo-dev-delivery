"""Exercise the actual stdio server and a bound native task without a model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime.native_session import NativeSession


class NativeMcpTests(unittest.TestCase):
    def test_real_stdio_handshake_tools_and_start_bind_the_actual_user_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);w=root/'workspace';w.mkdir();(w/'input.json').write_text('{"value":42}')
            reviewer=root/'review.py';reviewer.write_text('import unittest\nclass Goal(unittest.TestCase):\n def test_case(self):self.assertTrue(True)\n')
            session='mcp-test-'+uuid.uuid4().hex;runtime=NativeSession(root/'native')
            runtime.hook({'hook_event_name':'UserPromptSubmit','session_id':session,'cwd':str(w),'prompt':'Original user goal, not a model-rewritten goal.'})
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
                {'jsonrpc':'2.0','method':'notifications/initialized'},
                {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'native_start','arguments':{'session_id':session,'workspace':str(w),'inputs':[str(w/'input.json')],'outputs':[str(w/'out.json')],'review_script':str(reviewer)}}},
                {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'shell','arguments':{'command':'unauthorized'}}}]
            process=subprocess.run([sys.executable,str(ROOT/'portable-agent-kit/scripts/native_mcp.py')],
                input=''.join(json.dumps(item)+'\n' for item in requests),capture_output=True,encoding='utf-8',timeout=15)
            self.assertEqual(0,process.returncode,process.stderr)
            responses=[json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual([1,2,3,4],[item['id'] for item in responses])
            self.assertEqual('2025-03-26',responses[0]['result']['protocolVersion'])
            self.assertEqual({'native_start','native_status','native_verify'},{tool['name'] for tool in responses[1]['result']['tools']})
            self.assertFalse(responses[2]['result']['isError']);self.assertTrue(responses[3]['result']['isError'])
            self.assertEqual('Original user goal, not a model-rewritten goal.',runtime.status(session)['binding']['goal'])
            self.assertEqual(['out.json'],runtime.status(session)['binding']['outputs'])
