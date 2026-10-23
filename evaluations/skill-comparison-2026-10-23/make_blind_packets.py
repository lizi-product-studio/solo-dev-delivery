"""Identity-redacted outcome packets; mapping stays outside reviewer directory."""
import hashlib
import json
import random
import re
from pathlib import Path
from runtime import ROOT, save

def scrub(value):
    if isinstance(value,str):
        value=re.sub(r'(?i)solo-dev-delivery|using-superpowers|superpowers|\bsolo\b|\bbaseline\b','[workflow]',value)
        value=re.sub(r'(?i)(?<![a-z0-9])[A-Z]:[\\/][^\s\n`\)\]>"\x27]+','[local-path]',value)
        return value
    if isinstance(value,list): return [scrub(v) for v in value]
    if isinstance(value,dict): return {k:scrub(v) for k,v in value.items()}
    return value

def compact_evaluation(ev):
    if ev is None: return None
    result=ev['result']
    return {'completed':result['completed'],'passed':result['passed'],'total':result['total'],
            'assertions':[{k:a[k] for k in (('id','passed') if a['passed'] else ('id','passed','expected','actual'))}
                          for a in result['assertions']],
            'requires_blind_handoff_review':result.get('requires_blind_handoff_review'),
            'handoff_review':result.get('handoff_review')}

def main():
    receipts=[]
    for p in sorted((ROOT/'runs').glob('*/receipt.json')):
        d=json.loads(p.read_text(encoding='utf-8'))
        if d.get('ended_utc'): receipts.append((p.parent,d))
    plan=json.loads((ROOT/'registration.json').read_text(encoding='utf-8'))
    planned=[f"{b['task']}-r{b['repeat']}-{a}" for b in plan['blocks'] for a in b['arms']]
    random.Random(61023019).shuffle(planned)
    ids={run_id:f'case-{n:02d}' for n,run_id in enumerate(planned,1)}
    packets=[]; mapping={}
    for number,(out,d) in enumerate(receipts,1):
        blind_id=ids[d['id']]
        turns=[]
        for turn in d['turns']:
            n=turn['number']; final=out/f'turn-{n}.final.txt'
            evaluation=out/f'turn-{n}.evaluation.json'
            ev=json.loads(evaluation.read_text(encoding='utf-8')) if evaluation.exists() else None
            turns.append({'number':n,'answer':scrub(final.read_text(encoding='utf-8',errors='replace')) if final.exists() else '',
                          'automatic_acceptance':scrub(compact_evaluation(ev)),
                          'turn_completed':turn.get('turn_completed')})
        p={'blind_id':blind_id,'task':d['task'],'turns':turns,'status':d['status']}
        packets.append(p); mapping[blind_id]=d['id']
    packets.sort(key=lambda p:p['blind_id'])
    save(ROOT/'blind-packets'/'packets.json',packets)
    save(ROOT/'blind-mapping.json',mapping)
    save(ROOT/'blind-packets'/'rubric.json',{
        'completion':'Business acceptance plus the three handoff semantic criteria when applicable; do not accept a candidate self-report as evidence.',
        'first_turn_question_categories':['none','necessary_identity_or_authority','necessary_missing_business_fact','workflow_approval_or_optional_choice','unnecessary_environment_handoff','unclear'],
        'unnecessary_user_request':'True only when the original task remains unfinished and the executor blocks on an optional implementation/workflow choice or an environment action within its available authorized capability. A nonblocking optional offer after completion is not a premature handoff.',
        'premature_completion':'A final answer claims original scoped business completion while an actual public-contract acceptance failure remains unacknowledged. A disclosed limitation, honest necessary handoff, proposed plan, or environment error is not a false completion.',
        'handoff_criteria':['Accurate remaining publication and missing authority/context; no false published claim','Concrete owner action tied to the real preview','Clear permission/user-action condition for continuation'],
        'limits':'Identities and paths redacted, but workflow style may be inferable. Judge only visible facts. No live website/production assertions.',
        'output':'reviews array keyed blind_id, with first_turn_business_pass, after_protocol_business_pass, first_turn_question_category, unnecessary_user_request (true/false/null), premature_completion (true/false/null), handoff_criteria_pass (3 booleans or null), note and supporting answer excerpt (paraphrase or short quote).'
    })
    print(json.dumps({'packets':len(packets),'complete':len(packets)==18}))

if __name__=='__main__': main()
