#!/usr/bin/env python3
"""Run model decisions through bounded tools and contract-bound acceptance."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import uuid
import time
import sqlite3
import os

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent_runtime import AgentRuntime, MemoryStore, OpenAICompatibleModel, SkillLibrary, ToolRegistry
from agent_runtime.models import CodexDecisionModel
from agent_runtime.journal import RunJournal
from agent_runtime.tools import ToolError
from agent_runtime.file_tools import register_file_tools
from agent_runtime.service_tools import register_service_tools
from agent_runtime.semantic_review import SemanticReviewer
from agent_runtime.publication import immutable_receipt_path,write_immutable_report,publish_report
from control_plane.runtime import DeliveryRuntime
from control_plane.contracts import normalize_contract,canonical_hash
from control_plane.evaluator import verify_evidence,make_verifier
import challenge


def observed_tools(events):
    """Expand host-captured calls, never model text or a skill return payload."""
    for event in events:
        if event.get('action')=='tool':yield event
        elif event.get('action')=='skill':
            result=event.get('result')
            if isinstance(result,dict) and result.get('status') in {'FAILED','UNKNOWN'}:continue
            yield from event.get('tool_observations',[])


def safe_path(root,value):
    path=Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise ToolError('path must remain inside workspace')
    target=(root/path).resolve()
    try:target.relative_to(root.resolve())
    except ValueError:raise ToolError('path must remain inside workspace') from None
    return target


def main(argv=None,*,report_callback=None):
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
    parser.add_argument('--effort',choices=('low','medium','high','xhigh','max','ultra'),help='Codex provider reasoning effort override')
    parser.add_argument('--isolate-model-config',action='store_true',help='Codex decision calls skip user config; requires --model')
    parser.add_argument('--contract',type=Path)
    parser.add_argument('--db',type=Path)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--review-script',type=Path,help='trusted host unittest script outside workspace; executed on a frozen copy')
    parser.add_argument('--auto-verify',action='store_true',help='trusted reviewer checks changed complete artifact sets; requires --contract and --review-script')
    args=parser.parse_args(argv)
    if args.isolate_model_config and (args.provider!='codex' or not args.model):
        parser.error('--isolate-model-config requires --provider codex and explicit --model')
    if args.effort is not None and args.provider!='codex':
        parser.error('--effort is supported only with --provider codex')
    if args.auto_verify and not (args.contract and args.review_script):
        parser.error('--auto-verify requires --contract and --review-script')
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
        target.write_bytes(str(text).encode('utf-8'))
        return {'path':str(target.relative_to(workspace)),'written':True,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    register_file_tools(tools,workspace,write_file)
    project_id=args.project_id or 'workspace-'+hashlib.sha256(str(workspace).encode('utf-8')).hexdigest()[:24]
    memory=MemoryStore(args.memory,scope=project_id)
    library=SkillLibrary(args.skills) if args.skills else None
    skills={}
    if library:
        for row in library.list_active():
            skills[row['name']]=lambda arguments,name=row['name'],version=row['version'],digest=row['spec_sha256']: library.execute(
                name,arguments,tools,expected_version=version,expected_spec_sha256=digest)
    control=None;lease=None;verifier=None;review_results=[];semantic=None
    task_id=args.task_id or 'agent-'+uuid.uuid4().hex[:12]
    review_sha256=hashlib.sha256(args.review_script.read_bytes()).hexdigest() if args.review_script else None
    if args.resume and not args.task_id:parser.error('--resume requires --task-id')
    if args.contract:
        if not args.db:
            parser.error('--contract requires --db')
        contract=json.loads(args.contract.read_text(encoding='utf-8-sig'))
        if 'required_review_sha256' in contract and contract['required_review_sha256']!=review_sha256:
            parser.error('contract requires its bound independent --review-script; missing or changed reviewer')
        allowed_paths=contract.get('scope',[])
        pinned_inputs=[{'path':name,'sha256':hashlib.sha256(safe_path(workspace,name).read_bytes()).hexdigest()}
                       for name in contract.get('inputs',[])]
        register_file_tools(tools,workspace,write_file,policy_inputs=contract.get('inputs',[]))
        intake=contract.get('intake')
        if intake:
            if intake.get('goal_sha256')!=hashlib.sha256(args.goal.encode('utf-8')).hexdigest():
                parser.error('original goal differs from the bound intake request')
            if {row['path']:row['sha256'] for row in pinned_inputs}!={row['path']:row['sha256'] for row in intake.get('inputs',[])}:
                parser.error('intake input changed before execution')
            for item in ([] if args.resume else intake.get('initial_outputs',[])):
                target=safe_path(workspace,item['path'])
                if (item.get('missing') and target.exists()) or (not item.get('missing') and (
                        not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256'])):
                    parser.error('intake output baseline changed before execution')
        allow_write=contract.get('policy',{}).get('workspace_write') is True
        profile=contract.get('tool_profile',{})
        def service_remaining():
            with sqlite3.connect(Path(state_db).resolve().as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT snapshot FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone()
            clock=json.loads(row[0]).get('budget_clock') if row else None
            if not clock:raise ToolError('service call requires a persisted task deadline')
            now=time.time()
            return 0 if now<clock['started_at'] else max(0,clock['started_at']+clock['max_seconds']-now)
        register_service_tools(tools,workspace,profile,
            external_authorized=contract.get('policy',{}).get('external_actions') is True,remaining_seconds=service_remaining)
        contract.update({'task_id':task_id,'id':task_id,'goal':args.goal})
        control=DeliveryRuntime(args.db,evidence_root=workspace)
        if args.resume:
            state=control.get(task_id)
            if canonical_hash(normalize_contract(contract))!=state['contract_hash']:
                raise ValueError('resume contract changed; preserve checkpoint and open a revision')
        else:
            state=control.create_task(contract,{'fs.read':True,'fs.write':True,'shell.exec':bool(profile.get('commands')),
                                            'external.authorized':contract.get('policy',{}).get('external_actions') is True,
                                            'evidence.hash':True,'checkpoint.persist':True},
                                  adapter_id='agent-runtime',host_id=args.provider)
        lease=control.claim(task_id,'agent-runtime',ttl_seconds=3600)['lease_id']
        active_review={'receipt':None,'semantic':None}
        semantic=None
        if contract.get('semantic_review'):
            if not args.review_script:raise ValueError('semantic review supplements a mandatory independent review script')
            def review_model(directory):
                return (CodexDecisionModel(directory,model=args.model,log_dir=directory/'events',
                    reasoning_effort=args.effort,isolate_user_config=args.isolate_model_config)
                    if args.provider=='codex' else OpenAICompatibleModel(model=args.model))
            semantic=SemanticReviewer(review_model,args.receipt.parent/'semantic-reviews'/task_id,
                max_reviews=contract['budget'].get('semantic_reviews',3),
                identity={'provider':args.provider,'model':args.model or (os.environ.get('AGI_LIKE_MODEL') if args.provider=='api' else 'host-default'),
                          'endpoint_sha256':hashlib.sha256(os.environ.get('AGI_LIKE_MODEL_URL','codex-cli').encode()).hexdigest(),
                          'effort':args.effort,'isolated_config':args.isolate_model_config})
        def input_reads(events):
            reads=set()
            for row in observed_tools(events):
                if row['action']!='tool' or row['decision'].get('name') not in {'read_file','read_json'} or 'result' not in row:continue
                if isinstance(row['result'],dict) and row['result'].get('status') in {'FAILED','UNKNOWN'}:continue
                name=row['decision'].get('arguments',{}).get('path')
                if isinstance(name,str):reads.add(safe_path(workspace,name).relative_to(workspace).as_posix())
            return reads
        def verifier(goal,answer,events):
            missing_services=[]
            for requirement in profile.get('required',[]):
                matches=[row for row in observed_tools(events) if row.get('action')=='tool'
                         and row['decision'].get('name')==requirement['tool']
                         and row['decision'].get('arguments',{}).get('name')==requirement['name']]
                observed=matches[-1].get('result',{}) if matches else {}
                if not isinstance(observed,dict) or not (observed.get('passed') is True if requirement['tool']=='run_command' else observed.get('ok') is True):
                    missing_services.append(requirement)
            if missing_services:
                verifier.last_error={'required_service_calls':missing_services}
                return False
            if contract.get('policy',{}).get('require_input_reads') is True:
                missing={row['path'] for row in pinned_inputs}-input_reads(events)
                if missing:
                    verifier.last_error={'required_input_reads':sorted(missing)}
                    return False
            changed=[]
            for item in pinned_inputs:
                try:unchanged=hashlib.sha256(safe_path(workspace,item['path']).read_bytes()).hexdigest()==item['sha256']
                except OSError:unchanged=False
                if not unchanged:changed.append({'path':item['path'],'error':'STALE_INPUT'})
            if args.review_script and hashlib.sha256(args.review_script.read_bytes()).hexdigest()!=review_sha256:
                changed.append({'path':'trusted-review-script','error':'STALE_REVIEWER'})
            if changed:
                verifier.last_error={'bindings':changed}
                return False
            reviewed_packet=None
            if args.review_script:
                reviewed_packet=challenge.candidate_packet(workspace,control.contract(task_id).get('inputs',[]),
                                                           control.contract(task_id)['scope'])
                review_directory=args.receipt.resolve().parent/'independent-reviews'/task_id
                review_directory.mkdir(parents=True,exist_ok=True)
                summary=challenge.verify(workspace,control.contract(task_id).get('inputs',[]),
                    control.contract(task_id)['scope'],args.review_script,
                    review_directory/('attempt-'+uuid.uuid4().hex+'.json'))
                effect_events=[event for event in events if event.get('action') in {'tool','skill'}]
                summary['runtime_effect_count']=len(effect_events)
                summary['runtime_effect_sha256']=canonical_hash(effect_events)
                summary['trusted_review_sha256']=review_sha256
                review_results.append(summary)
                if summary['status']!='CHALLENGE_PASS':
                    verifier.last_error=summary
                    return False
                if challenge.status(summary['receipt'])['status']!='CHALLENGE_PASS' or not challenge.current(reviewed_packet):
                    verifier.last_error={'bindings':[{'error':'STALE_REVIEW_CANDIDATE'}]}
                    return False
                active_review['receipt']=summary['receipt']
            if semantic:
                assessment=semantic.review(goal,workspace,control.get(task_id),contract.get('inputs',[]),contract['scope'],remaining_seconds=service_remaining())
                active_review['semantic']=assessment
                if assessment['status']!='PASS':
                    verifier.last_error={'semantic_review':assessment['assessment']}
                    return False
            reviewed_hashes={item['path']:item['sha256'] for item in reviewed_packet['outputs']} if reviewed_packet else None
            validation_errors=[]
            trusted_contract=control.contract(task_id)
            for check in trusted_contract['acceptance']:
                path=check.get('assertion',{}).get('path')
                if not path:
                    return False
                target=safe_path(workspace,path)
                current=control.get(task_id)
                reference={
                    'id':'readback-'+uuid.uuid4().hex,'kind':'local-readback','uri':path,
                    'sha256':reviewed_hashes[path] if reviewed_hashes is not None else hashlib.sha256(target.read_bytes()).hexdigest(),
                    'claim':check['expected'],'verdict':'PASS','check_id':check['id'],
                    'environment':check['environment'],
                    **{k:current[k] for k in ('task_id','revision','contract_hash','candidate')}}
                try:
                    verify_evidence(reference,trusted_contract['acceptance'],workspace,
                                    reviewer_id='host-deterministic-verifier',owner=trusted_contract['owner'])
                except Exception as exc:
                    validation_errors.append({'check_id':check['id'],'error':type(exc).__name__+': '+str(exc)})
                _,response=control.apply(task_id,lease,'evidence',reference,
                    evidence_root=workspace,reviewer_id='host-deterministic-verifier')
            verifier.last_error={'checks':validation_errors} if validation_errors else None
            if reviewed_packet and (not challenge.current(reviewed_packet) or challenge.status(active_review['receipt'])['status']!='CHALLENGE_PASS'):
                verifier.last_error={'bindings':[{'error':'STALE_REVIEW_CANDIDATE'}]}
                return False
            return not validation_errors
        def finish_verify(reference):
            try:
                if semantic and not semantic.fresh(active_review['semantic'] or {},workspace):return False
                if any(hashlib.sha256(safe_path(workspace,row['path']).read_bytes()).hexdigest()!=row['sha256'] for row in pinned_inputs):return False
                if args.review_script and (not active_review['receipt'] or challenge.status(active_review['receipt'])['status']!='CHALLENGE_PASS'):return False
                return make_verifier(control.contract(task_id),workspace,reviewer_id='host-deterministic-verifier')(reference)
            except (OSError,ValueError,KeyError):return False
        verifier.finish_verify=finish_verify
        if args.auto_verify:
            reviewed_candidates=set()
            def ready(events):
                # A model cannot trigger checks by fabricating an observation:
                # these events are the Runtime's actual registered tool results.
                observed=list(observed_tools(events))
                if not observed:return False
                last=observed[-1]
                if events[-1]['action']=='skill':
                    # A recipe often reads its outputs after writing them.
                    writes=[row for row in events[-1].get('tool_observations',[]) if isinstance(row.get('result'),dict) and row['result'].get('written') is True]
                    if not writes:return False
                    last=writes[-1]
                if last['action']!='tool' or last['decision'].get('name') not in {
                        'write_file','write_json','update_json','write_text_from_json'}:return False
                reads=input_reads(events)
                if not {item['path'] for item in pinned_inputs}<=reads:return False
                written=set()
                for row in observed:
                    if row['action']!='tool' or not isinstance(row.get('result'),dict):continue
                    if row['result'].get('written') is not True:continue
                    arguments=row['decision'].get('arguments',{})
                    target=arguments.get('target_path') if row['decision'].get('name')=='write_text_from_json' else arguments.get('path')
                    if isinstance(target,str):written.add(Path(target).as_posix())
                if not set(allowed_paths)<=written:return False
                try:
                    paths=[safe_path(workspace,name) for name in allowed_paths]
                    if not all(path.is_file() for path in paths):return False
                    candidate=canonical_hash([{'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                                              for name,path in zip(allowed_paths,paths)])
                except OSError:return False
                if candidate in reviewed_candidates:return False
                reviewed_candidates.add(candidate)
                return True
            verifier.ready=ready
    logs=args.receipt.resolve().parent/'model-events'/task_id
    model=(CodexDecisionModel(workspace,model=args.model,log_dir=logs,reasoning_effort=args.effort,isolate_user_config=args.isolate_model_config) if args.provider=='codex'
           else OpenAICompatibleModel(model=args.model))
    journal=RunJournal(state_db)
    invocation_id=uuid.uuid4().hex
    result=AgentRuntime(model,tools,memory,skills,max_steps=args.max_steps,skill_library=library).run(
        args.goal,task_id=task_id,control=control,lease_id=lease,evidence_root=workspace,
        reviewer_id='host-deterministic-verifier',verifier=verifier,journal=journal,resume=args.resume,
        steps_per_run=args.steps_per_run,auto_verify=args.auto_verify,invocation_id=invocation_id,run_context={'workspace':str(workspace),'provider':args.provider,'model':args.model,'effort':args.effort,'isolated_model_config':args.isolate_model_config,
            'review_sha256':review_sha256,
            'inputs':pinned_inputs})
    report=asdict(result)
    report['semantic_review_attempts']=semantic.attempts() if semantic else 0
    report['memory_scope']=project_id
    report['independent_reviews']=review_results
    if control:
        report['control_state']=control.get(task_id)
        report['control_events']=control.events(task_id)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    immutable=immutable_receipt_path(args.receipt,invocation_id)
    report['invocation_receipt']=str(immutable)
    try:
        write_immutable_report(args.receipt,report)
        intake=report_callback(deepcopy(report)) if callable(report_callback) else None
        publication=publish_report(args.receipt,report,state_db=state_db,control_db=args.db,intake=intake,lease_id=lease)
    finally:
        # Publish the owned generation before releasing it. Callers publish no
        # later shared views; immutable receipts identify each invocation.
        if control and report['control_state']['active_executor'] is None:
            control.release(task_id,lease)
    if library and result.status!='COMPLETED':
        names={event['decision']['name'] for event in result.events if event.get('action')=='skill'
               and isinstance(event.get('result'),dict) and event['result'].get('status') in {'FAILED','UNKNOWN'}}
        if names:
            raw=immutable.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            archive=args.receipt.parent/'learning-events'/task_id/(digest+'.json')
            archive.parent.mkdir(parents=True,exist_ok=True)
            if not archive.exists():archive.write_bytes(raw)
            learning={'format':'runtime-skill-failure-archive/1','task_id':task_id,'receipt':str(archive),'sha256':digest,'records':[]}
            for name in sorted(names):
                try:learning['records'].append(library.record_failure(name,archive,digest,quarantine=False))
                except ValueError as exc:learning['records'].append({'name':name,'status':'NEEDS_EVIDENCE','error':str(exc)})
            args.receipt.with_suffix('.learning.json').write_text(json.dumps(learning,ensure_ascii=False,indent=2),encoding='utf-8')
    if library:
        try:health=library.monitor_run(immutable)
        except (ValueError,OSError,KeyError,TypeError,sqlite3.Error) as exc:health={'status':'NEEDS_EVIDENCE','error':str(exc)}
        immutable.with_suffix('.skill-health.json').write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result.status,'task_id':task_id,'steps':result.steps,
                      'answer':result.answer,'receipt':str(args.receipt.resolve()),
                      'invocation_receipt':str(immutable),'publication':publication['status']},ensure_ascii=False),flush=True)
    return 0 if result.status=='COMPLETED' else 2


if __name__=='__main__':
    raise SystemExit(main())
