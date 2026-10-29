"""One real-model delivery using actual named process + local HTTP adapters."""
import argparse,hashlib,json,subprocess,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',required=True)
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    work=root/'workspace';work.mkdir();requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(self.path);self.send_response(200);self.end_headers()
            self.wfile.write(b'{"items":[{"qty":5},{"qty":7}]}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    goal='Read TASK.md. Call the approved inventory HTTP operation and project_check command. Write report.json with total=sum of inventory item qty and check_passed=true only if the actual command passed. Do not modify project.py or test_project.py.'
    (work/'TASK.md').write_text(goal,encoding='utf-8')
    (work/'project.py').write_text('def total(values): return sum(values)\n',encoding='utf-8')
    (work/'test_project.py').write_text('import unittest,project\nclass Check(unittest.TestCase):\n def test_total(self):self.assertEqual(7,project.total([2,5]))\n',encoding='utf-8')
    profile={'commands':{'project_check':{'argv':[sys.executable,'-m','unittest','test_project'],'timeout':30}},
             'http':{'inventory':{'url':'http://127.0.0.1:'+str(server.server_port)+'/inventory'}},
             'required':[{'tool':'run_command','name':'project_check'},{'tool':'http_request','name':'inventory'}]}
    save(root/'profile.json',profile)
    review=root/'review.py';review.write_text('import unittest,json\nfrom pathlib import Path\nclass Delivery(unittest.TestCase):\n def test_report(self):\n  r=json.loads(Path("report.json").read_text())\n  self.assertEqual(12,r["total"])\n  self.assertIs(True,r["check_passed"])\n',encoding='utf-8')
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in work.iterdir()}
    try:
        process=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_task.py'),
            '--goal',goal,'--workspace',str(work),'--allow-write','report.json',
            '--control-root',str(root/'control'),'--project-store',str(root/'learning'),
            '--review-script',str(review),'--tool-profile',str(root/'profile.json'),
            '--authorization-ref','user-authorized-local-service-acceptance','--model',args.model,
            '--effort','medium','--isolate-model-config','--max-steps','16','--max-seconds','600'],
            capture_output=True,encoding='utf-8',timeout=900)
        (root/'stdout.log').write_text(process.stdout,encoding='utf-8');(root/'stderr.log').write_text(process.stderr,encoding='utf-8')
        report=json.loads((root/'control/intake-receipt.json').read_text(encoding='utf-8'))
        runtime=json.loads((root/'control/receipt.json').read_text(encoding='utf-8')) if (root/'control/receipt.json').exists() else {}
        calls=[e for e in runtime.get('events',[]) if e['action']=='tool']
        command=any(e['decision'].get('name')=='run_command' and e.get('result',{}).get('passed') is True for e in calls)
        api=any(e['decision'].get('name')=='http_request' and e.get('result',{}).get('ok') is True for e in calls)
        unchanged=all(hashlib.sha256((work/name).read_bytes()).hexdigest()==digest for name,digest in before.items())
        result={'status':'PASS' if process.returncode==0 and report['status']=='COMPLETED' and command and api and unchanged else 'INCOMPLETE',
                'runtime_status':report['status'],'command_observed':command,'http_observed':api,'http_requests':requests,
                'inputs_unchanged':unchanged,'total_decision_steps':report.get('total_decision_steps'),
                'limits':'Real requested model, actual local HTTP and project unittest process; synthetic data, no production integration claim.'}
        save(root/'result.json',result);print(json.dumps(result),flush=True)
        return 0 if result['status']=='PASS' else 2
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':raise SystemExit(main())
