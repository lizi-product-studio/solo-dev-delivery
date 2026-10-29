"""Direct adapter checks using real local processes and a disposable HTTP server."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable-agent-kit'))
from agent_runtime.tools import ToolRegistry,ToolError
from agent_runtime.service_tools import register_service_tools


class ServiceToolsTests(unittest.TestCase):
    def test_named_process_observes_exit_and_rejects_arbitrary_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            tools=ToolRegistry()
            register_service_tools(tools,temp,{'commands':{'check':{'argv':[sys.executable,'-c','print("ready")']}}})
            result=tools.call('run_command',{'name':'check'})
            self.assertTrue(result['passed']);self.assertIn('ready',result['stdout']['text'])
            with self.assertRaises(ToolError):tools.call('run_command',{'name':'arbitrary-shell'})

    def test_real_http_is_bound_redacted_and_never_redirects_or_retries_writes(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/unexpected');self.end_headers();return
                self.send_response(200);self.end_headers();self.wfile.write(b'{"total":42}')
            def do_POST(self):
                requests.append({'id':self.headers.get('Idempotency-Key'),'auth':self.headers.get('Authorization')})
                self.send_response(503 if self.path=='/uncertain' else 200);self.end_headers()
                self.wfile.write(json.dumps({'token':self.headers.get('Authorization')}).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            profile={'http':{'read':{'url':base+'/read'},'redirect':{'url':base+'/redirect'},
                'write':{'url':base+'/write','method':'POST','auth':{'env':'TEST_SERVICE_KEY'}},
                'uncertain':{'url':base+'/uncertain','method':'POST'}}}
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'TEST_SERVICE_KEY':'synthetic-secret-123'}):
                with self.assertRaises(ToolError):register_service_tools(ToolRegistry(),temp,profile)
                tools=ToolRegistry();register_service_tools(tools,temp,profile,external_authorized=True)
                self.assertEqual(42,tools.call('http_request',{'name':'read'})['data']['total'])
                self.assertFalse(tools.call('http_request',{'name':'redirect'})['ok'])
                self.assertNotIn('/unexpected',requests)
                with self.assertRaises(ToolError):tools.call('http_request',{'name':'write','body':{'n':1}})
                tools.execution_context={'action_id':'task-step-1'}
                result=tools.call('http_request',{'name':'write','body':{'n':1}})
                self.assertNotIn('synthetic-secret-123',json.dumps(result));self.assertTrue(result['operation_id'])
                tools.execution_context={'action_id':'task-step-2'}
                self.assertEqual('UNKNOWN',tools.call('http_request',{'name':'uncertain'})['status'])
                self.assertEqual(2,len([r for r in requests if isinstance(r,dict)]))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
