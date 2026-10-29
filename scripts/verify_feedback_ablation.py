#!/usr/bin/env python3
"""Frozen same-model independent-test-feedback ablation, not framework ranking."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import sys

from verify_domain_runtime import run_task,FIXTURES,ROOT
sys.path.insert(0,str(ROOT/'portable-agent-kit/scripts'))
import challenge


def usage(root):
    calls=list((root/'model-events').rglob('process.json'))
    counts=[]
    for path in (root/'model-events').rglob('stdout.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('type')=='turn.completed' and isinstance(row.get('usage'),dict):
                value=row['usage']
                if all(type(value.get(k)) is int for k in ('input_tokens','output_tokens')):counts.append(value)
    complete=bool(calls) and len(counts)==len(calls)
    return {'model_calls':len(calls),'usage_known_calls':len(counts),'usage_complete':complete,
            'input_tokens':sum(r['input_tokens'] for r in counts) if complete else None,
            'output_tokens':sum(r['output_tokens'] for r in counts) if complete else None,
            'cached_input_tokens':sum(r.get('cached_input_tokens',0) for r in counts) if complete else None,
            'cost_usd':None}


def failure_class(report):
    """Censor only identifiable adapter failures, never task/model prose.

    Runtime currently preserves model exceptions as MODEL_ERROR plus their
    string. CodexCliAdapter has a fixed timeout message and wraps nonzero CLI
    exits with the JSON error events. Without those boundaries and an explicit
    error code, an infrastructure attribution is unknown and stays observed.
    """
    if report.get('status')!='MODEL_ERROR':return None
    message=report.get('answer')
    if not isinstance(message,str):return None
    if message=='Codex timeout; result unknown, inspect persisted logs before retry':
        return 'transport_or_timeout'
    prefix=re.match(r'^Codex exit -?\d+: ',message)
    if not prefix:return None
    try:events,_=json.JSONDecoder().raw_decode(message[prefix.end():])
    except ValueError:return None
    if not isinstance(events,list):return None
    classes={
        'capacity_or_quota':{'capacity_error','server_overloaded','model_overloaded',
                            'rate_limit_exceeded','usage_limit_reached','insufficient_quota',429},
        'identity_or_permission':{'unauthorized','authentication_error','invalid_api_key',
                                  'permission_denied','permission_error',401,403},
        'transport_or_timeout':{'connection_error','transport_error','network_error',
                                'request_timeout',408,502,503,504}}
    def codes(value):
        if not isinstance(value,dict):return
        for key in ('code','error_code','status_code','http_status','codex_error_info'):
            code=value.get(key)
            if isinstance(code,str) or type(code) is int:yield code
        for key in ('error','codex_error_info'):
            yield from codes(value.get(key))
    for event in events:
        if not isinstance(event,dict) or event.get('type') not in {'error','turn.failed'}:continue
        for code in codes(event):
            for label,known in classes.items():
                if code in known:return label
    return None


def trial(task,parent,arm,model,effort,max_steps,isolate_model_config=False):
    result=run_task(task,parent,model,effort=effort,review_enabled=arm=='feedback',max_steps=max_steps,isolate_model_config=isolate_model_config)
    root=parent/task['id'];report={}
    if (root/'receipt.json').exists():report=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
    infrastructure=failure_class(report or result)
    try:
        # Both arms receive this same final grader. The baseline never sees it
        # until after its last model decision; grading does not rerun the agent.
        final=challenge.verify(root/'workspace',task['inputs'],task['outputs'],root/'review.py',root/'final-review/receipt.json')
        verdict=final['status']
    except Exception as exc:
        final={'status':'FAIL','error':type(exc).__name__+': '+str(exc)};verdict='FAIL'
    frozen=json.loads((root/'freeze.json').read_text(encoding='utf-8'))
    oracle_bound=hashlib.sha256((root/'review.py').read_bytes()).hexdigest()==frozen['review_sha256']
    outcome={**result,'arm':arm,'requested_model':model,'reasoning_effort':effort,
             'max_steps':max_steps,'final_review':final,'infrastructure_failure':infrastructure,
             'isolated_model_config':isolate_model_config,
             'tool_calls':sum(e.get('action')=='tool' for e in report.get('events',[])),
             'finish_proposals':sum(e.get('action')=='finish' for e in report.get('events',[])),
             'model_error':report.get('answer') if report.get('status')=='MODEL_ERROR' else None,
             'task_success':bool(report.get('status')=='COMPLETED' and verdict=='CHALLENGE_PASS'
                    and result.get('source_unchanged') and result.get('required_input_reads') and oracle_bound
                    and infrastructure is None),
             'usage':usage(root),'limits':'Same-runtime feedback ablation; not raw LLM vs whole AGI-like.'}
    (root/'ablation-result.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2),encoding='utf-8')
    return outcome


def summarize(rows,expected_pairs=None):
    pairs={}
    for row in rows:
        key=(row['id'],row['repetition']);arms=pairs.setdefault(key,{})
        if row['arm'] in arms:raise ValueError('duplicate trial arm')
        arms[row['arm']]=row
    valid=[];censored=[]
    for key,arms in sorted(pairs.items()):
        mismatch=any(len({r.get(field) for r in arms.values()})>1 for field in ('requested_model','reasoning_effort','max_steps','isolated_model_config'))
        if set(arms)!={'baseline','feedback'} or mismatch or any(r.get('infrastructure_failure') for r in arms.values()):
            censored.append({'task':key[0],'repetition':key[1],'arms':arms});continue
        valid.append(arms)
    wins=sum(not p['baseline']['task_success'] and p['feedback']['task_success'] for p in valid)
    losses=sum(p['baseline']['task_success'] and not p['feedback']['task_success'] for p in valid)
    complete=expected_pairs is None or len(pairs)==expected_pairs
    return {'format':'feedback-ablation/1','status':'OBSERVED' if valid and not censored and complete else 'INCOMPLETE',
            'valid_pairs':len(valid),'censored_pairs':len(censored),'feedback_wins':wins,'feedback_losses':losses,
            'baseline_successes':sum(p['baseline']['task_success'] for p in valid),
            'feedback_successes':sum(p['feedback']['task_success'] for p in valid),
            'censored':censored,'trials':rows,
            'limits':'Small descriptive ablation. Both arms use AGI-like Runtime. Cannot establish general model improvement, AGI, cost savings or superiority over other frameworks.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--effort',default='medium');parser.add_argument('--max-steps',type=int,default=16)
    parser.add_argument('--repetitions',type=int,default=1);parser.add_argument('--seed',type=int,default=29)
    parser.add_argument('--isolate-model-config',action='store_true')
    parser.add_argument('--task',action='append',help='explicit task IDs for a focused mechanism replay')
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((FIXTURES/'manifest.json').read_text(encoding='utf-8'))
    if args.task:
        known={task['id'] for task in manifest['tasks']}
        if set(args.task)-known:raise ValueError('unknown task ID')
        manifest['tasks']=[task for task in manifest['tasks'] if task['id'] in args.task]
    rng=random.Random(args.seed);plans=[]
    for rep in range(1,args.repetitions+1):
        for task in manifest['tasks']:
            order=['baseline','feedback'];rng.shuffle(order);plans.append((rep,task,order))
    protocol={'format':'feedback-ablation-protocol/1','model':args.model,'effort':args.effort,
              'max_steps':args.max_steps,'seed':args.seed,'repetitions':args.repetitions,
              'isolated_model_config':args.isolate_model_config,
              'source_hashes':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                               for folder in ('portable-agent-kit/agent_runtime','portable-agent-kit/control_plane','portable-agent-kit/scripts')
                               for p in (ROOT/folder).glob('*.py')},
              'initial_workspace_hashes':{t['id']:{p.relative_to(FIXTURES/t['workspace']).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in (FIXTURES/t['workspace']).rglob('*') if p.is_file()} for t in manifest['tasks']},
              'arms':{'baseline':'no independent test feedback during run','feedback':'trusted unittest feedback, max 2 repairs'},
              'common':'same Runtime, goal, file tools, contract and final evaluator; cold isolated memory, no active skills',
              'task_hashes':{t['id']:{kind:hashlib.sha256((FIXTURES/t[kind]).read_bytes()).hexdigest()
                                    for kind in ('review','contract','goal_path')} for t in manifest['tasks']},
              'order':[{'repetition':rep,'task':task['id'],'arms':order} for rep,task,order in plans]}
    (root/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    def pair(plan):
        rep,task,order=plan;result=[]
        for arm in order:
            parent=root/('r%d'%rep)/arm;parent.mkdir(parents=True,exist_ok=True)
            row=trial(task,parent,arm,args.model,args.effort,args.max_steps,args.isolate_model_config);row['repetition']=rep
            result.append(row);print(json.dumps({'task':task['id'],'arm':arm,'success':row['task_success'],
                                               'runtime':row.get('runtime_status'),'infrastructure':row['infrastructure_failure']},ensure_ascii=False),flush=True)
        return result
    rows=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(pair,plan) for plan in plans]):
            rows.extend(future.result())
            (root/'progress.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=summarize(rows,expected_pairs=len(plans))
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('trials','censored')},ensure_ascii=False,indent=2),flush=True)
    return 0 if summary['status']=='OBSERVED' else 2


if __name__=='__main__':raise SystemExit(main())
