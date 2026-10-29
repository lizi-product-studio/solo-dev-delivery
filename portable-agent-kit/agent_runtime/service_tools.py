"""Host-approved named processes and HTTP operations. No arbitrary model shell/URL.

Process execution inherits the user's OS permissions; this is not an OS sandbox.
Uncertain writes/timeouts remain UNKNOWN and are never automatically retried.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .tools import ToolError

LIMIT=1024*1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _positive(value,name,maximum=300):
    if type(value) not in (int,float) or not 0<value<=maximum:raise ToolError(name+' is out of bounds')
    return value


def register_service_tools(tools,workspace,profile,*,external_authorized=False,remaining_seconds=None):
    if not isinstance(profile,dict) or set(profile)-{'commands','http','required'}:raise ToolError('invalid host tool profile')
    commands=profile.get('commands',{});operations=profile.get('http',{})
    if not all(isinstance(value,dict) and len(value)<=32 for value in (commands,operations)):
        raise ToolError('tool profile must contain bounded named maps')
    workspace=Path(workspace).resolve()
    prepared={}
    for name,definition in commands.items():
        if not isinstance(name,str) or not name or not isinstance(definition,dict) or set(definition)-{'argv','timeout'}:
            raise ToolError('invalid named command')
        argv=definition.get('argv')
        if not isinstance(argv,list) or not argv or len(argv)>64 or any(not isinstance(v,str) or '\x00' in v for v in argv):
            raise ToolError('command argv must be a fixed string list')
        executable=shutil.which(argv[0])
        if not executable:raise ToolError('approved command executable is unavailable: '+name)
        prepared[name]={'argv':[executable,*argv[1:]],'timeout':_positive(definition.get('timeout',60),'command timeout')}
    for name,op in operations.items():
        if not isinstance(name,str) or not name or not isinstance(op,dict) or set(op)-{'url','method','timeout','auth'}:
            raise ToolError('invalid named HTTP operation')
        url=urlsplit(op.get('url',''))
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.fragment:
            raise ToolError('HTTP operation requires a fixed HTTP(S) URL without embedded credentials')
        if not isinstance(op.get('method','GET'),str) or op.get('method','GET') not in {'GET','POST','PUT','PATCH','DELETE'}:raise ToolError('unsupported HTTP method')
        if op.get('method','GET')!='GET' and not external_authorized:raise ToolError('external writes are not authorized')
        _positive(op.get('timeout',30),'HTTP timeout')
        if 'auth' in op:
            auth=op['auth']
            if not isinstance(auth,dict) or set(auth)-{'env','header','prefix'} or not isinstance(auth.get('env'),str):
                raise ToolError('auth must refer to a host environment variable')
            if any(not isinstance(auth.get(k,''),str) or '\n' in auth.get(k,'') or '\r' in auth.get(k,'') for k in ('header','prefix')):
                raise ToolError('invalid auth header configuration')
    def timeout(limit):
        remaining=remaining_seconds() if callable(remaining_seconds) else None
        if remaining is not None:
            if remaining<=0:raise ToolError('task wall-time budget exhausted')
            return min(limit,remaining)
        return limit
    def run_command(name):
        if not isinstance(name,str) or name not in prepared:raise ToolError('command is not in the host profile')
        command=prepared[name];duration=timeout(command['timeout'])
        env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE') if k in os.environ}
        env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            try:
                process=subprocess.Popen(command['argv'],cwd=workspace,env=env,stdin=subprocess.DEVNULL,
                    stdout=stdout,stderr=stderr,start_new_session=True,**flags)
            except OSError as exc:raise ToolError('approved process could not start: '+str(exc)) from exc
            try:code=process.wait(timeout=duration)
            except subprocess.TimeoutExpired:
                sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
                from challenge import _terminate_test_tree
                cleanup=_terminate_test_tree(process)
                return {'status':'UNKNOWN','error':'approved command timed out; inspect effects before retry','cleanup':cleanup}
            def tail(stream):
                stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-LIMIT))
                return {'text':stream.read(LIMIT).decode('utf-8',errors='replace'),'bytes':size,'truncated':size>LIMIT}
            return {'status':'SUCCEEDED','exit_code':code,'passed':code==0,'stdout':tail(stdout),'stderr':tail(stderr),
                    'meaning':'Process invocation observed; exit code and independent acceptance decide task correctness.'}
    def http_request(name,body=None):
        if not isinstance(name,str) or name not in operations:raise ToolError('HTTP operation is not in the host profile')
        op=operations[name];method=op.get('method','GET');duration=timeout(op.get('timeout',30))
        if method=='GET' and body is not None:raise ToolError('GET operation does not accept a body')
        try:data=None if body is None else json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        except (ValueError,TypeError) as exc:raise ToolError('HTTP body must be finite JSON') from exc
        if data is not None and len(data)>LIMIT:raise ToolError('HTTP body exceeds 1 MiB')
        headers={'Accept':'application/json','Content-Type':'application/json'};secret=None
        if op.get('auth'):
            auth=op['auth'];secret=os.environ.get(auth['env'])
            if not secret:raise ToolError('required host credential is unavailable')
            if '\n' in secret or '\r' in secret:raise ToolError('host credential contains invalid header characters')
            headers[auth.get('header','Authorization')]=auth.get('prefix','Bearer ')+secret
        identity=None
        if method!='GET':
            action=getattr(tools,'execution_context',{}).get('action_id')
            if not action:raise ToolError('external write requires a bound Runtime action')
            identity=hashlib.sha256((action+'/'+name).encode()).hexdigest();headers['Idempotency-Key']=identity
        request=Request(op['url'],data=data,headers=headers,method=method)
        try:
            with build_opener(NoRedirect()).open(request,timeout=duration) as response:
                raw=response.read(LIMIT+1);code=response.status
            if len(raw)>LIMIT:
                return {'status':'UNKNOWN' if method!='GET' else 'SUCCEEDED','ok':False,'http_status':code,'error':'response exceeds 1 MiB','operation_id':identity}
            text=raw.decode('utf-8',errors='replace')
            if secret:text=text.replace(secret,'[REDACTED]')
            try:value=json.loads(text)
            except ValueError:value=text
            return {'status':'SUCCEEDED','ok':200<=code<300,'http_status':code,'data':value,'operation_id':identity}
        except Exception as exc:
            # A write may have committed before an error response or timeout.
            if method!='GET':return {'status':'UNKNOWN','operation_id':identity,'error':'HTTP write outcome requires readback','error_type':type(exc).__name__}
            return {'status':'SUCCEEDED','ok':False,'http_status':exc.code if isinstance(exc,HTTPError) else None,'error_type':type(exc).__name__}
    required=profile.get('required',[])
    if not isinstance(required,list) or len(required)>64:raise ToolError('invalid required service calls')
    for item in required:
        if not isinstance(item,dict) or set(item)!={'tool','name'}:raise ToolError('required service calls need tool and name')
        catalog=prepared if item['tool']=='run_command' else operations if item['tool']=='http_request' else {}
        if not isinstance(item['name'],str) or item['name'] not in catalog:raise ToolError('required service call is not registered')
    if prepared:tools.register('run_command',run_command,description='execute a fixed host-approved command by name; no model shell or arguments. exit_code must be checked. Approved names: '+json.dumps(list(prepared)),effects='unknown')
    if operations:tools.register('http_request',http_request,description='call a fixed host-approved HTTP operation by name; optional JSON body, no arbitrary URL or headers. Approved operations: '+json.dumps({name:op.get('method','GET') for name,op in operations.items()}),effects='read' if all(op.get('method','GET')=='GET' for op in operations.values()) else 'unknown')
