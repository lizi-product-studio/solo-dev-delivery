"""Join blinded judgments with fixed run IDs only after judgments are recorded."""
import hashlib
import json
import statistics
from pathlib import Path
from runtime import ROOT, save

def main():
    mapping=json.loads((ROOT/'blind-mapping.json').read_text(encoding='utf-8'))
    files=['blind-review-a-r2.json','blind-review-b1.json','blind-review-b2.json','blind-review-b3.json']
    reviews=[]; seen=set()
    for name in files:
        value=json.loads((ROOT/name).read_text(encoding='utf-8'))
        items=value['reviews'] if isinstance(value,dict) else value
        for item in items:
            blind_id=item['blind_id']
            if blind_id in seen: raise ValueError('Duplicate blind judgment: '+blind_id)
            seen.add(blind_id)
            reviews.append(dict(item,run_id=mapping[blind_id],review_file=name))
    if len(reviews)!=18: raise ValueError('Require all 18 independent judgments')
    save(ROOT/'blind-review-results.json',{'kind':'identity_redacted_review_not_double_blind','reviews':reviews,
        'source_files':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files},
        'notes':['Initial presentation and first review retained; URL presentation revision changed no candidate or automatic scores.',
                 'Question-category labeling differs for conditional necessary owner handoffs; primary unnecessary-blocking-request booleans agree with rubric. Do not rank groups by this auxiliary label.']})
    receipts={}
    for p in (ROOT/'runs').glob('*/receipt.json'):
        d=json.loads(p.read_text(encoding='utf-8')); receipts[d['id']]=d
    groups={}
    for arm in ('baseline','solo','superpowers'):
        raw=[r for r in receipts.values() if r['arm']==arm]
        judged=[r for r in reviews if receipts[r['run_id']]['arm']==arm]
        assert len(raw)==len(judged)==6
        for result in judged:
            r=receipts[result['run_id']]
            if result['first_turn_business_pass'] is True: assert r['turns'][0]['business_completed'] is True
            if result['after_protocol_business_pass'] is True: assert r['turns'][-1]['business_completed'] is True
        times=[r['execution_seconds'] for r in raw]
        usage=[r['metrics']['observed_total_usage'] for r in raw]
        totals={key:sum(u[key] for u in usage) if all(type(u.get(key)) is int for u in usage) else None
                for key in ('input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','reasoning_output_tokens')}
        groups[arm]={'samples':6,'first_turn_pass':sum(r['first_turn_business_pass'] is True for r in judged),
          'protocol_pass':sum(r['after_protocol_business_pass'] is True for r in judged),
          'unnecessary_user_request':sum(r['unnecessary_user_request'] is True for r in judged),
          'premature_completion':sum(r['premature_completion'] is True for r in judged),
          'synthetic_user_replies':sum(r['synthetic_user_replies'] for r in raw),
          'execution_seconds_sum':sum(times),'execution_seconds_mean':statistics.mean(times),'execution_seconds_median':statistics.median(times),
          'observed_usage_sum':totals,'observed_usage_mean':{k:v/6 if v is not None else None for k,v in totals.items()},
          'warnings':[w for r in raw for w in r['metrics'].get('warnings',[])],
          'handoffs_passed':sum(r['handoff_criteria_pass']==[True,True,True] for r in judged)}
    save(ROOT/'comparison-stats.json',{'groups':groups,'coordinator_setup_and_review_usage':None,
        'overhead_note':'Setup, authoring, orchestration and independent review excluded from per-arm execution; not measured as a complete total and not claimed zero. Smoke calibration separately measured.'})
    print(json.dumps(groups,ensure_ascii=True))

if __name__=='__main__':main()
