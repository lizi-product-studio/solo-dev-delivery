#!/usr/bin/env python3
"""Connect the current native Codex session to Runtime acceptance and memory."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent_runtime.native_session import NativeSession,save,session_home


def brief(result):
    keys=('status','task_id','session_id','root','hook_observed','tool_count','repairs','max_repairs','user_messages','pending','tool_observation_coverage','reason','last_receipt','reused','relevant_experience','recovery','artifacts')
    return {key:result[key] for key in keys if key in result} if result.get('task_id') else result


def install_hooks(codex_home,native_home):
    target=Path(codex_home).expanduser().resolve()/'hooks.json'
    previous=target.read_bytes() if target.exists() else None
    config=json.loads(previous.decode('utf-8-sig')) if previous else {'hooks':{}}
    if not isinstance(config,dict) or not isinstance(config.get('hooks',{}),dict):raise ValueError('existing hooks config is not an object')
    hooks=config.setdefault('hooks',{})
    script=Path(__file__).resolve()
    args=[sys.executable,str(script),'--home',str(Path(native_home).resolve()),'hook']
    command=' '.join(shlex.quote(arg) for arg in args)
    literal=lambda value:"'"+str(value).replace("'","''")+"'"
    windows=("$ErrorActionPreference = 'Stop'; $PSNativeCommandUseErrorActionPreference = $false; "
        "$LASTEXITCODE = $null; try { & "+' '.join(literal(arg) for arg in args)+
        "; if ($null -eq $LASTEXITCODE) { throw 'Native hook did not return an exit code' }; exit $LASTEXITCODE } catch { [Console]::Error.WriteLine($_); exit 1 }")
    for event in ('UserPromptSubmit','PreToolUse','PostToolUse','Stop','Interrupt'):
        groups=hooks.setdefault(event,[])
        retained=[]
        for group in groups:
            entries=[h for h in group.get('hooks',[]) if h.get('statusMessage')!='AGI-like native Runtime']
            if entries:retained.append({**group,'hooks':entries})
        retained.append({'hooks':[{'type':'command','command':command,'commandWindows':windows,
            'timeout':90 if event=='Stop' else 3 if event=='Interrupt' else 8,'statusMessage':'AGI-like native Runtime'}]})
        hooks[event]=retained
    if previous:
        backup=Path(native_home)/'hook-backups'/('hooks-'+uuid.uuid4().hex+'.json');backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(previous)
    target.parent.mkdir(parents=True,exist_ok=True);save(target,config)
    return {'status':'CONFIGURED_REQUIRES_HOST_TRUST','hooks':str(target),'native_home':str(Path(native_home).resolve()),
            'events':list(('UserPromptSubmit','PreToolUse','PostToolUse','Stop','Interrupt')),
            'next':'Review the AGI-like native Runtime hook definitions in Codex /hooks; no trust database or managed policy was modified.'}


def main(argv=None):
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path)
    sub=parser.add_subparsers(dest='operation',required=True)
    begin=sub.add_parser('begin');begin.add_argument('--session-id',default=os.environ.get('CODEX_THREAD_ID'))
    begin.add_argument('--workspace',type=Path,default=Path.cwd());begin.add_argument('--goal')
    begin.add_argument('--input',action='append',default=[]);begin.add_argument('--output',action='append',required=True)
    begin.add_argument('--review-script',type=Path,required=True)
    begin.add_argument('--max-repairs',type=int,default=2);begin.add_argument('--max-tools',type=int,default=100);begin.add_argument('--max-seconds',type=int,default=3600)
    for operation in ('status','verify','readback','recover'):
        child=sub.add_parser(operation);child.add_argument('--session-id',default=os.environ.get('CODEX_THREAD_ID'))
    sub.add_parser('hook')
    installer=sub.add_parser('install-hooks');installer.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    args=parser.parse_args(argv)
    if args.home is None:
        args.home=Path(os.environ['AGI_LIKE_NATIVE_HOME']) if os.environ.get('AGI_LIKE_NATIVE_HOME') else session_home(getattr(args,'session_id',None) or os.environ.get('CODEX_THREAD_ID',''))
    try:
        if args.operation=='install-hooks':result=install_hooks(args.codex_home,args.home)
        else:
            runtime=NativeSession(args.home)
            if args.operation=='hook':
                raw=sys.stdin.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('hook input too large')
                result=runtime.hook(json.loads(raw))
            elif args.operation=='begin':result=runtime.begin(args.session_id,args.workspace,args.input,args.output,args.review_script,
                goal=args.goal,max_repairs=args.max_repairs,max_tools=args.max_tools,max_seconds=args.max_seconds)
            elif args.operation=='verify':result=runtime.verify(args.session_id)
            elif args.operation=='readback':result=runtime.readback(args.session_id)
            elif args.operation=='recover':result=runtime.recover(args.session_id)
            else:result=runtime.status(args.session_id)
        if args.operation in {'begin','status','verify','readback','recover'} and result.get('task_id'):
            displayed=brief(result)
        else:displayed=result
        print(json.dumps(displayed,ensure_ascii=False,indent=None if args.operation=='hook' else 2),flush=True)
        return 2 if args.operation=='verify' and result.get('status') not in {'COMPLETED','VERIFICATION_QUEUED'} else 0
    except Exception as exc:
        if args.operation=='hook':
            print(json.dumps({'systemMessage':'AGI-like native hook could not establish acceptance: '+type(exc).__name__+': '+str(exc)},ensure_ascii=False),flush=True)
        else:print(json.dumps({'status':'REJECTED','error':type(exc).__name__+': '+str(exc)},ensure_ascii=False),flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
