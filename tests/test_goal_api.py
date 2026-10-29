"""One public natural-goal API path, using a labelled loopback model double."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class GoalApiTests(unittest.TestCase):
    def test_api_plans_executes_and_independently_accepts_without_codex(self):
        requests=[]
        class Model(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                request=json.loads(payload['messages'][1]['content']);requests.append(request)
                if 'submit_plan' in request['tools']:
                    decision={'action':'tool','name':'submit_plan','arguments':{'inputs':['TASK.md'],
                        'outputs':['report.json'],'steps':['read task','write total'],'unknowns':[]}}
                elif not request['observations']:decision={'action':'tool','name':'read_file','arguments':{'path':'TASK.md'}}
                else:decision={'action':'tool','name':'write_json','arguments':{'path':'report.json','value':{'total':42}}}
                response=json.dumps({'choices':[{'message':{'content':json.dumps(decision)}}]}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(response)
        server=ThreadingHTTPServer(('127.0.0.1',0),Model);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);work=root/'workspace';work.mkdir();(work/'TASK.md').write_text('Write total42.',encoding='utf-8')
                (root/'review.py').write_text('import unittest,json\nfrom pathlib import Path\nclass R(unittest.TestCase):\n def test_total(self):self.assertEqual(42,json.loads(Path("report.json").read_text())["total"])\n',encoding='utf-8')
                process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_task.py'),
                    '--provider','api','--goal','Write total42.','--workspace',str(work),'--allow-write','report.json',
                    '--control-root',str(root/'control'),'--project-store',str(root/'learning'),
                    '--authorization-ref','local-api-fixture','--review-script',str(root/'review.py')],
                    env={**os.environ,'AGI_LIKE_MODEL_URL':'http://127.0.0.1:'+str(server.server_port),
                         'AGI_LIKE_MODEL':'local-double','AGI_LIKE_API_KEY':'synthetic-key',
                         'NO_PROXY':'127.0.0.1,localhost','CODEX_CLI_PATH':'not-a-real-codex'},
                    capture_output=True,encoding='utf-8',timeout=20)
                self.assertEqual(0,process.returncode,process.stderr+process.stdout)
                result=json.loads(process.stdout);self.assertEqual('COMPLETED',result['status'])
                self.assertEqual(3,result['total_decision_steps']);self.assertEqual(3,len(requests))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
