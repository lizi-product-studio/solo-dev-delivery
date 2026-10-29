#!/usr/bin/env python3
"""Run model decisions through bounded tools and contract-bound acceptance."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent_runtime import AgentRuntime, MemoryStore, OpenAICompatibleModel, SkillLibrary, ToolRegistry
from agent_runtime.models import CodexDecisionModel
from agent_runtime.journal import RunJournal
from agent_runtime.tools import ToolError
from control_plane.runtime import DeliveryRuntime
from control_plane.contracts import normalize_contract,canonical_hash
import challenge


def safe_path(root,value):
    path=Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise ToolError('path must remain inside workspace')
    target=(root/path).resolve()
    try:target.relative_to(root.resolve())
    except ValueError:raise ToolError('path must remain inside workspace') from None
    return target


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal',required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--memory',type=Path,required=True)
    parser.add_argument('--project-id',help='memory scope; defaults to a hash of the resolved workspace')
    parser.add_argument('--skills',type=Path)
    parser.add_argument('--max-steps',type=int,default=12)
    parser.add_argument('--task-id')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--steps-per-run',type=int)
    parser.add_argument('--state-db',type=Path,help='loop checkpoint database; defaults to control db or receipt.state.sqlite3')
    parser.add_argument('--provider',choices=('codex','api'),default='codex')
    parser.add_argument('--model')
    parser.add_argument('--contract',type=Path)
    parser.add_argument('--db',type=Path)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--review-script',type=Path,help='trusted host unittest script outside workspace; executed on a frozen copy')
    args=parser.parse_args(argv)
    if not 1<=args.max_steps<=1000:parser.error('--max-steps must be 1..1000')
    if args.steps_per_run is not None and args.steps_per_run<1:parser.error('--steps-per-run must be positive')
    workspace=args.workspace.resolve()
    state_db=args.state_db or args.db or args.receipt.with_suffix('.state.sqlite3')
    for control_path in (state_db,args.db,args.memory,args.skills,args.receipt):
        if control_path is not None and control_path.resolve().is_relative_to(workspace):
            raise ValueError('control files must stay outside the tool workspace')
    workspace.mkdir(parents=True,exist_ok=True)
    if args.review_script and args.review_script.resolve().is_relative_to(workspace):
        raise ValueError('trusted review script must be outside the tool workspace')
    allowed_paths=None;pinned_inputs=[]
    allow_write=False
    tools=ToolRegistry()
    tools.register('read_file',lambda path: safe_path(workspace,path).read_text(encoding='utf-8'),description='read a UTF-8 workspace file; arguments: path')
    def write_file(path,text):
        if not allow_write:
            raise ToolError('workspace writes require an authorized contract policy')
        target=safe_path(workspace,path)
        relative=target.relative_to(workspace).as_posix()
        if allowed_paths is not None and not any(
                relative==scope or (scope.endswith('/') and relative.startswith(scope))
                for scope in allowed_paths):
            raise ToolError('write is outside the contract scope')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(str(text),encoding='utf-8')
        return {'path':str(target.relative_to(workspace)),'written':True}
    tools.register('write_file',write_file,description='write a UTF-8 workspace file; arguments: path, text')
    project_id=args.project_id or 'workspace-'+hashlib.sha256(str(workspace).encode('utf-8')).hexdigest()[:24]
    memory=MemoryStore(args.memory,scope=project_id)
    library=SkillLibrary(args.skills) if args.skills else None
    skills={}
    if library:
        for row in library.list_active():
            skills[row['name']]=lambda arguments,name=row['name']: library.execute(name,arguments,tools)
    control=None;lease=None;verifier=None;review_results=[]
    task_id=args.task_id or 'agent-'+uuid.uuid4().hex[:12]
    if args.resume and not args.task_id:parser.error('--resume requires --task-id')
    if args.contract:
        if not args.db:
            parser.error('--contract requires --db')
        contract=json.loads(args.contract.read_text(encoding='utf-8-sig'))
        allowed_paths=contract.get('scope',[])
        pinned_inputs=[{'path':name,'sha256':hashlib.sha256(safe_path(workspace,name).read_bytes()).hexdigest()}
                       for name in contract.get('inputs',[])]
        allow_write=contract.get('policy',{}).get('workspace_write') is True
        contract.update({'task_id':task_id,'id':task_id,'goal':args.goal})
        control=DeliveryRuntime(args.db,evidence_root=workspace)
        if args.resume:
            state=control.get(task_id)
            if canonical_hash(normalize_contract(contract))!=state['contract_hash']:
                raise ValueError('resume contract changed; preserve checkpoint and open a revision')
        else:
            state=control.create_task(contract,{'fs.read':True,'fs.write':True,'shell.exec':False,
                                            'evidence.hash':True,'checkpoint.persist':True},
                                  adapter_id='agent-runtime',host_id=args.provider)
        lease=control.claim(task_id,'agent-runtime',ttl_seconds=3600)['lease_id']
        def verifier(goal,answer,events):
            if args.review_script:
                review_directory=args.receipt.resolve().parent/'independent-reviews'/task_id
                review_directory.mkdir(parents=True,exist_ok=True)
                summary=challenge.verify(workspace,control.contract(task_id).get('inputs',[]),
                    control.contract(task_id)['scope'],args.review_script,
                    review_directory/('attempt-'+uuid.uuid4().hex+'.json'))
                review_results.append(summary)
                if summary['status']!='CHALLENGE_PASS':
                    verifier.last_error=summary
                    return False
            for check in control.contract(task_id)['acceptance']:
                path=check.get('assertion',{}).get('path')
                if not path:
                    return False
                target=safe_path(workspace,path)
                current=control.get(task_id)
                _,response=control.apply(task_id,lease,'evidence',{
                    'id':'readback-'+uuid.uuid4().hex,'kind':'local-readback','uri':path,
                    'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                    'claim':check['expected'],'verdict':'PASS','check_id':check['id'],
                    'environment':check['environment'],
                    **{k:current[k] for k in ('task_id','revision','contract_hash','candidate')}},
                    evidence_root=workspace,reviewer_id='host-deterministic-verifier')
            return True
    logs=args.receipt.resolve().parent/'model-events'/task_id
    model=(CodexDecisionModel(workspace,model=args.model,log_dir=logs) if args.provider=='codex'
           else OpenAICompatibleModel(model=args.model))
    journal=RunJournal(state_db)
    result=AgentRuntime(model,tools,memory,skills,max_steps=args.max_steps,skill_library=library).run(
        args.goal,task_id=task_id,control=control,lease_id=lease,evidence_root=workspace,
        reviewer_id='host-deterministic-verifier',verifier=verifier,journal=journal,resume=args.resume,
        steps_per_run=args.steps_per_run,run_context={'workspace':str(workspace),'provider':args.provider,'model':args.model,
            'review_sha256':hashlib.sha256(args.review_script.read_bytes()).hexdigest() if args.review_script else None,
            'inputs':pinned_inputs})
    report=asdict(result)
    report['memory_scope']=project_id
    report['independent_reviews']=review_results
    if control:
        report['control_state']=control.get(task_id)
        report['control_events']=control.events(task_id)
        if report['control_state']['active_executor'] is None:
            control.release(task_id,lease)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result.status,'task_id':task_id,'steps':result.steps,
                      'answer':result.answer,'receipt':str(args.receipt.resolve())},ensure_ascii=False),flush=True)
    return 0 if result.status=='COMPLETED' else 2


if __name__=='__main__':
    raise SystemExit(main())
