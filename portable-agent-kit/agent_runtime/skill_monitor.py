"""Attribute an independently reviewed candidate to its last uninterrupted recipe.

This establishes observed compatibility, not a proven causal diagnosis. Unknown
effects, subsequent edits and partial-output recipes are never auto-quarantined.
"""
import hashlib
from pathlib import Path

from .failure_learning import _read
from control_plane.contracts import canonical_hash


def observe_health(path):
    raw,report=_read(path);state=report.get('control_state',{})
    outcome=report.get('status')
    if outcome not in {'COMPLETED','FAILED','CONTROL_BLOCKED','BUDGET_EXHAUSTED'}:raise ValueError('run is not a known completed or failed observation')
    if not state.get('task_id') or state['task_id']!=report.get('task_id') or state.get('unknown_tool_calls'):raise ValueError('unknown or unbound control state')
    if outcome=='COMPLETED' and state.get('status')!='COMPLETED':raise ValueError('completion is not confirmed by the control plane')
    effects=[event for event in report.get('events',[]) if event.get('action') in {'tool','skill'}]
    if not effects:raise ValueError('no runtime actions')
    summaries=report.get('independent_reviews',[])
    if not summaries:raise ValueError('no independent test observation')
    summary=summaries[-1]
    count=summary.get('runtime_effect_count')
    if type(count) is not int or count<1 or count>len(effects):raise ValueError('review is not bound to the executed action prefix')
    prefix=effects[:count]
    if canonical_hash(prefix)!=summary.get('runtime_effect_sha256'):raise ValueError('review action binding changed')
    if any(e.get('action')!='tool' or e.get('decision',{}).get('name') not in {'read_file','read_json'} for e in effects[count:]):
        raise ValueError('candidate changed after its last independent review')
    while prefix and prefix[-1].get('action')=='tool' and prefix[-1].get('decision',{}).get('name') in {'read_file','read_json'}:prefix=prefix[:-1]
    if not prefix or prefix[-1].get('action')!='skill':raise ValueError('last candidate producer is not a skill')
    event=prefix[-1];binding=event.get('skill_binding',{})
    if (binding.get('name')!=event.get('decision',{}).get('name') or type(binding.get('version')) is not int
            or not isinstance(binding.get('spec_sha256'),str) or type(event.get('step')) is not int):raise ValueError('missing skill version binding')
    result=event.get('result')
    if isinstance(result,dict) and result.get('status','SUCCEEDED')!='SUCCEEDED':raise ValueError('skill did not have a known successful execution')
    action_id=state['task_id']+'-step-'+str(event['step'])
    if not any(row.get('operation')=='tool_result' and row.get('request',{}).get('task_id')==state['task_id']
        and all(row['request'].get(k)==state.get(k) for k in ('revision','contract_hash','candidate'))
        and row['request'].get('payload',{}).get('tool_call_id')==action_id
        and row['request']['payload'].get('status')=='SUCCEEDED' for row in report.get('control_events',[])):
        raise ValueError('no matching successful host control event')
    _,review=_read(summary['receipt'])
    expected='CHALLENGE_PASS' if outcome=='COMPLETED' else 'FAIL'
    if summary.get('status')!=expected or review.get('status')!=expected:raise ValueError('final run and review outcomes do not agree')
    cases=review.get('test_cases',[])
    if not cases or not review.get('test_summary',{}).get('tests'):raise ValueError('review executed no tests')
    if expected=='FAIL' and not any(c.get('status')=='FAIL' for c in cases):raise ValueError('no assertion failure; infrastructure errors are not skill regressions')
    if expected=='CHALLENGE_PASS' and any(c.get('status')!='PASS' for c in cases):raise ValueError('success contains nonpassing tests')
    packet_raw,packet=_read(review['packet'])
    if hashlib.sha256(packet_raw).hexdigest()!=review.get('packet_sha256'):raise ValueError('reviewed packet changed')
    if hashlib.sha256(Path(review['test']).read_bytes()).hexdigest()!=review.get('test_sha256'):raise ValueError('reviewer changed')
    if summary.get('trusted_review_sha256')!=review['test_sha256']:raise ValueError('reviewer is not bound to this task')
    if state['contract'].get('required_review_sha256') not in (None,review['test_sha256']):raise ValueError('reviewer differs from original task contract')
    written={}
    for call in event.get('tool_observations',[]):
        if call.get('decision',{}).get('name') not in {'read_file','read_json','write_file','write_json','update_json','project_json_fields','write_text_from_json'}:
            raise ValueError('recipe needs a domain effect monitor')
        value=call.get('result',{})
        if isinstance(value,dict) and value.get('written') is True:
            name=value.get('path')
            if isinstance(name,str):written[Path(name).as_posix()]=value.get('sha256')
    produced={item['path']:item['sha256'] for item in packet['outputs']}
    if not produced or written!=produced:raise ValueError('skill did not produce the complete independently reviewed output version')
    if set(produced)!=set(state['contract']['scope']):raise ValueError('review output set differs from task scope')
    return {**binding,'task_id':state['task_id'],'outcome':'PASS' if outcome=='COMPLETED' else 'FAIL',
            'action_id':action_id,'receipt_path':str(Path(path).resolve()),'receipt_sha256':hashlib.sha256(raw).hexdigest(),
            'review_sha256':hashlib.sha256(Path(summary['receipt']).read_bytes()).hexdigest()}
