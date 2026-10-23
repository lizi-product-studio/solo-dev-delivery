"""Explicit offline integration test: reviewed hooks in a disposable Codex home.

Uses the installed Codex lifecycle, not a hand-written shell substitute. The
documented one-invocation hook-trust override is confined to this empty test
home. No credentials are read/copied and no real model provider is used.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

SETUP = r'''import json,os,subprocess,sys
from pathlib import Path
package,root,mode=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
sys.path.insert(0,str(package/'scripts'))
import gate
raw=sys.stdin.buffer.read();payload=json.loads(raw.decode('utf-8'))
work,control,home=root/'work',root/'control',root/'home'
task=control/'task.json'
gate.start(work,['request.md'],['app.py'],task,session=payload['session_id'],home=home)
if mode not in ('pending','exit1','exit2'):
    adapter=control/'checks.py';plan=control/'plan.json'
    adapter.write_text('import unittest,app\nclass Business(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(app.value(7),7)\n  with self.assertRaises(ValueError): app.value(-1)\n',encoding='utf-8')
    units=[x['id'] for x in gate.read(control/'brief.json')['units']]
    gate.save(plan,{'test_file':str(adapter),'checks':[{'id':'actual','test':'Business.test_value','sources':units}],'context':[]})
    gate.seal(task,plan)
    good='def value(number):\n if number<0: raise ValueError("negative")\n return number\n'
    (work/'app.py').write_text(good if mode!='fail' else 'def value(number): return 0\n',encoding='utf-8')
    checked=gate.check(task,True)
    if checked['status']!=('BLOCKED' if mode=='fail' else 'DELIVERABLE_WITHIN_DECLARED_SCOPE'):
        raise RuntimeError('fixture acceptance failed: '+repr(checked))
    if mode=='stale': (work/'app.py').write_text('def value(number): return 0\n',encoding='utf-8')
query='[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $p='+str(os.getppid())+'; $rows=@(); for($i=0;$i -lt 4;$i++){ $x=Get-CimInstance Win32_Process -Filter ("ProcessId="+$p); if(-not $x){break}; $rows+=($x|Select-Object Name,ProcessId,ParentProcessId,ExecutablePath,CommandLine); if($x.Name -eq "codex.exe"){break}; $p=$x.ParentProcessId }; ConvertTo-Json -InputObject @($rows) -Compress'
observed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],capture_output=True,timeout=8,check=True)
chain=json.loads(observed.stdout.decode('utf-8'))
(root/'session.json').write_text(json.dumps({'session_id':payload['session_id'],'event':payload['hook_event_name'],'cwd':payload['cwd'],'stdin_sha256':__import__('hashlib').sha256(raw).hexdigest(),'parent_chain':chain},ensure_ascii=False),encoding='utf-8')
print('{}')
'''

def literal(value):
    return "'"+str(value).replace("'", "''")+"'"

def reviewed_command(parts):
    return "$ErrorActionPreference='Stop'; try { & "+' '.join(literal(p) for p in parts)+"; exit $LASTEXITCODE } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }"

def run_case(codex,package,root,mode):
    root.mkdir()
    home,work,control=root/'home',root/'work',root/'control'
    for path in (home,work,control):path.mkdir()
    (work/'request.md').write_text('Return the supplied number and reject negative values.\n',encoding='utf-8')
    (work/'app.py').write_text('# not implemented\n',encoding='utf-8')
    setup=root/'setup.py';setup.write_text(SETUP,encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','OPENAI_AUTH_TOKEN','AZURE_OPENAI_API_KEY','CODEX_THREAD_ID')}
    env.update(CODEX_HOME=str(home),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',ALL_PROXY='http://127.0.0.1:9',NO_PROXY='127.0.0.1,localhost')
    prepared=subprocess.run([sys.executable,str(package/'scripts/gate.py'),'install-hook','--home',str(home)],env=env,capture_output=True,timeout=10)
    if prepared.returncode:raise RuntimeError(prepared.stderr.decode('utf-8',errors='replace'))
    config=json.loads((home/'hooks.json').read_text(encoding='utf-8'))
    start=reviewed_command([sys.executable,setup,package,root,mode])
    config['hooks']['SessionStart']=[{'hooks':[{'type':'command','command':start,'commandWindows':start,'timeout':15}]}]
    if mode in ('exit1','exit2'):
        marker=root/'exit_probe.py'
        marker.write_text(r'''import hashlib,json,os,subprocess,sys
from pathlib import Path
raw=sys.stdin.buffer.read();payload=json.loads(raw.decode('utf-8'))
query='[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $p='+str(os.getppid())+'; $rows=@(); for($i=0;$i -lt 4;$i++){ $x=Get-CimInstance Win32_Process -Filter ("ProcessId="+$p); if(-not $x){break}; $rows+=($x|Select-Object Name,ProcessId,ParentProcessId,ExecutablePath,CommandLine); if($x.Name -eq "codex.exe"){break}; $p=$x.ParentProcessId }; ConvertTo-Json -InputObject @($rows) -Compress'
observed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],capture_output=True,timeout=8,check=True)
Path(sys.argv[1]).write_text(json.dumps({'event':payload['hook_event_name'],'session_id':payload['session_id'],'exit':int(sys.argv[2]),'stdin_sha256':hashlib.sha256(raw).hexdigest(),'parent_chain':json.loads(observed.stdout.decode('utf-8'))},ensure_ascii=False),encoding='utf-8')
print('HOOK_EXIT_'+sys.argv[2],file=sys.stderr)
sys.exit(int(sys.argv[2]))
''',encoding='utf-8')
        stop=reviewed_command([sys.executable,marker,root/'stop-observed.json',mode[-1]])
        config['hooks']['Stop']=[{'hooks':[{'type':'command','command':stop,'commandWindows':stop,'timeout':10}]}]
    (home/'hooks.json').write_text(json.dumps(config),encoding='utf-8')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(404);self.end_headers()
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            requests.append({'path':self.path,'body_bytes':len(body),'gate_feedback':b'Delivery is not verified' in body,'exit2_feedback':b'HOOK_EXIT_2' in body})
            if len(requests)>1:
                raw=json.dumps({'error':{'message':'Intentional local probe stop after observing hook feedback'}}).encode()
                self.send_response(503);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw);return
            item={'id':'msg_local','type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':'Local hook probe completed.','annotations':[]}]}
            events=[{'type':'response.created','response':{'id':'resp_local','output':[]}},{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':{'id':'resp_local','status':'completed','output':[item],'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}}]
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for event in events:self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (home/'config.toml').write_text('model="hook-probe"\nmodel_provider="local_hook_probe"\n[model_providers.local_hook_probe]\nname="Offline lifecycle probe"\nbase_url="http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api="responses"\nrequires_openai_auth=false\nrequest_max_retries=0\nstream_max_retries=0\n[analytics]\nenabled=false\n',encoding='utf-8')
    command=[str(codex),'--no-daemon','exec','--ephemeral','--json','--skip-git-repo-check','--sandbox','read-only','--dangerously-bypass-hook-trust','-C',str(work),'Return a short local probe message. Do not call tools.']
    started=time.monotonic()
    try:
        result=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
    finally:
        server.shutdown();server.server_close()
    (root/'stdout.jsonl').write_bytes(result.stdout);(root/'stderr.log').write_bytes(result.stderr)
    session=json.loads((root/'session.json').read_text(encoding='utf-8')) if (root/'session.json').exists() else None
    task=json.loads((control/'task.json').read_text(encoding='utf-8')) if (control/'task.json').exists() else None
    registrations=list((home/'delivery-gates').glob('*.json'))
    blocked=any(x['gate_feedback'] or (mode=='exit2' and x['exit2_feedback']) for x in requests[1:])
    if mode=='verified':passed=bool(session and task and task['state']=='DELIVERED' and not registrations and result.returncode==0 and len(requests)==1)
    elif mode=='exit1':passed=bool(session and result.returncode==0 and len(requests)==1)
    else:passed=bool(session and task and task['state']!='DELIVERED' and registrations and blocked and len(requests)>=2 and result.returncode!=0)
    chain=session.get('parent_chain',[]) if session else []
    host=chain[0] if chain else {}
    line=host.get('CommandLine','')
    actual_contract=host.get('Name','').lower() in ('pwsh.exe','powershell.exe') and '-noprofile' in line.lower() and '-command' in line.lower() and any(x.get('Name','').lower()=='codex.exe' for x in chain[1:])
    passed=passed and actual_contract
    stop_observed=None
    if mode in ('exit1','exit2'):
        stop_path=root/'stop-observed.json'
        observation=json.loads(stop_path.read_text(encoding='utf-8')) if stop_path.exists() else {}
        parents=observation.get('parent_chain',[])
        stop_host=parents[0] if parents else {}
        stop_line=stop_host.get('CommandLine','').lower()
        stop_observed=bool(session and observation.get('event')=='Stop' and observation.get('session_id')==session['session_id'] and observation.get('exit')==int(mode[-1]) and stop_host.get('Name','').lower() in ('pwsh.exe','powershell.exe') and '-noprofile' in stop_line and '-command' in stop_line and any(x.get('Name','').lower()=='codex.exe' for x in parents[1:]))
        passed=passed and stop_observed
    record={'mode':mode,'host_contract_observed':actual_contract,'host_executable':host.get('Name'),'stop_probe_observed':stop_observed,'pass':passed,'codex_exit':result.returncode,'elapsed_seconds':round(time.monotonic()-started,3),'session_start_observed':bool(session),'task_state':task.get('state') if task else None,'registrations_remaining':len(registrations),'requests':requests,'block_feedback_observed':blocked,'limits':'Nonzero CLI exit after a block is the deliberately failed second local response, not a successful completed model turn. Real lifecycle and gate are exercised; responses are deterministic, no real model call.'}
    (root/'result.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(record),flush=True)
    return record

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex',type=Path,required=True)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',default='exit1,exit2,pending,verified,stale,fail')
    args=parser.parse_args()
    if not set(args.cases.split(',')) <= {'exit1','exit2','pending','verified','stale','fail'}:parser.error('unknown case')
    if os.name!='nt':parser.error('requires a real Windows Codex runtime')
    if args.output.exists():parser.error('preserve existing evidence; choose a new output')
    args.output.mkdir(parents=True)
    version=subprocess.check_output([str(args.codex),'--version'],text=True).strip()
    results=[run_case(args.codex.resolve(),args.package.resolve(),args.output/name,name) for name in args.cases.split(',')]
    summary={'codex_version':version,'codex_sha256':hashlib.sha256(args.codex.read_bytes()).hexdigest(),'cases':results,'pass':all(x['pass'] for x in results),'trust_mode':'Reviewed test hooks, documented invocation-only override in a disposable CODEX_HOME; no persistent trust or global settings modified','model':'Loopback response stub, zero real model requests'}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return 0 if summary['pass'] else 1

if __name__=='__main__':sys.exit(main())
