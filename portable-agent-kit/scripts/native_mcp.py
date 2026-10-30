#!/usr/bin/env python3
"""Local stdio MCP facade for three existing native Runtime operations.

No shell, model requests, open network port, or business actions are exposed.
Codex owns the subprocess; JSON-RPC messages are newline-delimited UTF-8.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_runtime.native_session import NativeSession,session_home
from native_task import brief


SESSION={'type':'string','description':'Actual current Codex session id supplied by the native UserPromptSubmit hook.'}
TOOLS=[
    {'name':'native_start','description':'Start the current Codex conversation in AGI-like Runtime before editing. Reuses the actual original user prompt captured by hooks, existing independent acceptance, project memory and native tools. Does not start another model loop.',
     'inputSchema':{'type':'object','properties':{'session_id':SESSION,'workspace':{'type':'string','description':'Absolute business workspace from the actual user request; do not widen it to work around a path-format error.'},
        'inputs':{'type':'array','items':{'type':'string'},'description':'Read-only source file paths, relative to workspace or absolute within it.'},
        'outputs':{'type':'array','items':{'type':'string'},'minItems':1,'description':'Authorized deliverable file paths, relative to workspace or absolute within it.'},
        'review_script':{'type':'string','description':'Trusted independent unittest source, frozen before editing. An existing project test may be used when also declared in read-only inputs; otherwise supply an external reviewer.'},
        'max_repairs':{'type':'integer','minimum':0,'maximum':5},'max_tools':{'type':'integer','minimum':1,'maximum':1000},
        'max_seconds':{'type':'integer','minimum':1,'maximum':86400}},
        'required':['session_id','workspace','inputs','outputs','review_script'],'additionalProperties':False}},
    {'name':'native_status','description':'Read the current native Runtime task, observed hook connectivity, budgets and acceptance receipt.',
     'inputSchema':{'type':'object','properties':{'session_id':SESSION},'required':['session_id'],'additionalProperties':False}},
    {'name':'native_verify','description':'Request independent acceptance for this task. While a native tool call is active, queues verification for Stop; finish normally and repair any automatic feedback instead of polling this tool.',
     'inputSchema':{'type':'object','properties':{'session_id':SESSION},'required':['session_id'],'additionalProperties':False}}
]


def call(name,arguments):
    definition=next((tool for tool in TOOLS if tool['name']==name),None)
    if definition is None or not isinstance(arguments,dict):raise ValueError('unknown native Runtime tool')
    schema=definition['inputSchema']
    if set(arguments)-set(schema['properties']) or not set(schema['required'])<=set(arguments):raise ValueError('native tool arguments differ from the declared schema')
    for key,value in arguments.items():
        expected=schema['properties'][key]['type']
        if expected=='string' and (not isinstance(value,str) or not value.strip()):raise ValueError('expected nonempty '+key)
        if expected=='integer' and type(value) is not int:raise ValueError('expected integer '+key)
        if expected=='array' and (not isinstance(value,list) or any(not isinstance(item,str) for item in value)):raise ValueError('expected string array '+key)
    session_id=arguments['session_id'];runtime=NativeSession(session_home(session_id))
    if name=='native_start':
        result=runtime.begin(session_id,arguments['workspace'],arguments['inputs'],arguments['outputs'],arguments['review_script'],
            max_repairs=arguments.get('max_repairs',2),max_tools=arguments.get('max_tools',100),max_seconds=arguments.get('max_seconds',3600))
    elif name=='native_status':result=runtime.status(session_id)
    else:result=runtime.verify(session_id)
    return {'content':[{'type':'text','text':json.dumps(brief(result),ensure_ascii=False)}],
            'isError':result.get('status') in {'UNVERIFIED','NEEDS_RECONCILIATION','BUDGET_EXHAUSTED'}}


def handle(message):
    identifier=message.get('id');method=message.get('method');params=message.get('params',{})
    if identifier is None:return None
    try:
        if method=='initialize':
            requested=params.get('protocolVersion','2025-03-26')
            protocol=requested if requested in {'2024-11-05','2025-03-26','2025-06-18','2025-11-25'} else '2025-03-26'
            result={'protocolVersion':protocol,'capabilities':{'tools':{'listChanged':False}},
                'serverInfo':{'name':'agi-like-native-runtime','version':'1.9.1'},
                'instructions':'For AGI-like multi-step tasks use native_start with the session id supplied by hooks before editing. Keep native Codex tools; Stop independently verifies and returns repair feedback. Use these tools instead of trying to execute Skill scripts through a restricted shell.'}
        elif method=='ping':result={}
        elif method=='tools/list':result={'tools':TOOLS}
        elif method=='tools/call':
            try:result=call(params.get('name'),params.get('arguments',{}))
            except Exception as exc:result={'content':[{'type':'text','text':type(exc).__name__+': '+str(exc)}],'isError':True}
        else:return {'jsonrpc':'2.0','id':identifier,'error':{'code':-32601,'message':'Method not found'}}
        return {'jsonrpc':'2.0','id':identifier,'result':result}
    except Exception as exc:return {'jsonrpc':'2.0','id':identifier,'error':{'code':-32602,'message':str(exc)}}


def main():
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    while True:
        raw=sys.stdin.readline(2*1024*1024+1)
        if not raw:return
        try:
            if len(raw)>2*1024*1024:raise ValueError('MCP message exceeds 2 MiB')
            message=json.loads(raw)
            if not isinstance(message,dict):raise ValueError('MCP message must be an object')
            response=handle(message)
        except (ValueError,TypeError) as exc:response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':str(exc)}}
        if response is not None:print(json.dumps(response,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
