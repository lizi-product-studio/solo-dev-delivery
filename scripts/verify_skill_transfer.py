#!/usr/bin/env python3
"""Labelled local training/holdout execution + real model skill-use replay."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable-agent-kit'))
from agent_runtime import AgentRuntime,MemoryStore,SkillLibrary,ToolRegistry
from agent_runtime.regression import regress_recipe


def factory(root,case):
    workspace=root/case['id'];workspace.mkdir()
    tools=ToolRegistry()
    def path(name):
        target=(workspace/name).resolve();target.relative_to(workspace.resolve());return target
    def write_file(pathname=None,**kwargs):
        # Same parameter names and output shape as the public CLI file tool.
        name=kwargs['path'];text=kwargs['text']
        path(name).write_text(text,encoding='utf-8')
        return {'path':name,'written':True}
    tools.register('write_file',write_file)
    tools.register('read_file',lambda path: (workspace/path).read_text(encoding='utf-8'))
    return tools,workspace


def run(destination):
    root=Path(destination).resolve();root.mkdir(parents=True,exist_ok=False)
    library=SkillLibrary(root/'skills.sqlite3');memory=MemoryStore(root/'memory.sqlite3',scope='skill-transfer')
    goal='write and readback'
    training=[]
    for number in (1,2):
        case={'id':'train-%d'%number};tools,workspace=factory(root,case)
        value=json.dumps({'training_value':number})
        def model(request):
            if request['step']==1:return {'action':'tool','name':'write_file','arguments':{'path':'out.json','text':value}}
            if request['step']==2:return {'action':'tool','name':'read_file','arguments':{'path':'out.json'}}
            return {'action':'finish','answer':'readback verified'}
        result=AgentRuntime(model,tools,memory,skill_library=library).run(goal,task_id=case['id'],
            verifier=lambda g,a,e: (workspace/'out.json').read_text(encoding='utf-8')==value)
        training.append(asdict(result))
        if result.status!='COMPLETED':raise ValueError('training artifact did not pass')
    proposal=library.propose_from_repeated_success(goal)
    if not proposal:raise ValueError('recipe compilation failed')
    name=proposal['name'];cases=[]
    for i in (3,4):
        value=json.dumps({'holdout_value':i})
        cases.append({'id':'holdout-%d'%i,
                      'arguments':{'step1_path':'out.json','step1_text':value,'step2_path':'out.json'},
                      'expected':[{'tool':'write_file','result':{'path':'out.json','written':True}},
                                  {'tool':'read_file','result':value}]})
    receipt=regress_recipe(library,name,cases,lambda c:factory(root,c)[0],root/'holdout-receipts',reviewer_id='holdout-artifact-checker')
    library.activate(name,receipt)
    (root/'training.json').write_text(json.dumps(training,ensure_ascii=False,indent=2),encoding='utf-8')
    workspace=root/'real-task';workspace.mkdir()
    contract={'owner':'agent-runtime','candidate':'heldout-new-argument','authorization_ref':'local-task-test',
              'scope':['report.json'],'excluded':['external'],'policy':{'workspace_write':True,'shell_required':False},
              'acceptance':[{'id':'units','expected':'units=9','environment':'local',
                             'assertion':{'type':'json_pointer_equals','path':'report.json','pointer':'/units','value':9}}]}
    (root/'contract.json').write_text(json.dumps(contract),encoding='utf-8')
    prompt=('Use the ACTIVE skill '+name+' to write report.json containing the JSON object {"units":9}, '
            'then finish. Supply recipe parameters step1_path=report.json, step1_text as the JSON string, '
            'step2_path=report.json. The skill performs writing and readback; do not perform standalone tool calls.')
    command=[sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
             '--provider','codex','--goal',prompt,'--workspace',str(workspace),
             '--memory',str(root/'memory.sqlite3'),'--project-id','skill-transfer',
             '--skills',str(root/'skills.sqlite3'),'--db',str(root/'control.sqlite3'),
             '--contract',str(root/'contract.json'),'--receipt',str(root/'real-receipt.json'),'--max-steps','5']
    proc=subprocess.run(command,capture_output=True,encoding='utf-8',timeout=600)
    (root/'entry.stdout').write_text(proc.stdout,encoding='utf-8');(root/'entry.stderr').write_text(proc.stderr,encoding='utf-8')
    report=json.loads((root/'real-receipt.json').read_text(encoding='utf-8')) if (root/'real-receipt.json').exists() else {}
    passed=(proc.returncode==0 and report.get('status')=='COMPLETED' and
            any(e['action']=='skill' for e in report.get('events',[])) and library.get(name)['uses']>=1)
    summary={'status':'PASS' if passed else 'FAIL','training':'scripted decisions, actual file tools and host readback',
             'holdout':'two disjoint parameter cases, actual tool execution',
             'transfer':'real Codex JSON decisions via public agent_run CLI',
             'skill':name,'uses':library.get(name)['uses'],'task_status':report.get('status'),
             'actions':[e['action'] for e in report.get('events',[])],
             'limits':'Parameter transfer of one tool recipe only; not cross-domain AGI evidence.'}
    (root/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return 0 if passed else 2


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    raise SystemExit(run(parser.parse_args().output))
