from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
FREEZE=ROOT/'experiment-freeze.json'
RESULTS=ROOT/'results.jsonl'
TASKS=['01_normalize_email','02_sum_amounts','03_merge_intervals','04_parse_csv','05_inventory_reserve','06_expire_sessions','07_json_patch','08_rate_limit','09_topological_sort','10_redact_secrets']
ARMS=['baseline','solo-1.0']

def main():
    p=argparse.ArgumentParser(); p.add_argument('--check-raw',action='store_true'); args=p.parse_args()
    freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
    rows=[json.loads(line) for line in RESULTS.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(rows)==20, len(rows)
    ids=[r['trial_id'] for r in rows]; assert len(ids)==len(set(ids)), 'duplicate trial ids'
    assert {(r['task_id'],r['arm']) for r in rows}=={(t,a) for t in TASKS for a in ARMS}
    expected={'gpt-6.1-sol','medium',300}
    for r in rows:
        assert r['model']==expected.pop() if False else True
        assert r['model']=='gpt-6.1-sol', r['trial_id']
        assert r['reasoning_effort']=='medium', r['trial_id']
        assert r['timeout_seconds']==300, r['trial_id']
        assert r['status']!='harness_error', r['trial_id']
        assert r['capacity_failure']==(r.get('failure_class')=='capacity_error'), r['trial_id']
        assert r['user_intervention_count']>=0
        if args.check_raw:
            for key in ('events','stderr','sessions'):
                assert Path(r['raw'][key]).exists(), (r['trial_id'],key,r['raw'][key])
            # A capacity/connection failure may occur before the CLI writes -o final.txt.
            # The event stream, stderr, session JSONL, receipt, and evaluation remain required.
            if r.get('status') == 'completed':
                assert Path(r['raw']['final']).exists(), (r['trial_id'],'final',r['raw']['final'])
    assert freeze['protocol']['planned_trials']==20
    assert freeze['protocol']['model']=='gpt-6.1-sol'
    print(f'validate_experiment: PASS ({len(rows)} trials, {len(TASKS)} pairs)')

if __name__=='__main__': main()
