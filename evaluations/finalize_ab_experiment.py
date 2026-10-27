from __future__ import annotations
import hashlib, json, statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = Path(__file__).resolve().parent
OUT = EVAL / 'ab-solo-1.0-2026-10-26'
MATRIX = Path(r'C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-matrix')
REPAIR = Path(r'C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-matrix-repair')
TASKS_DIR = OUT / 'tasks'
TASKS = ['01_normalize_email','02_sum_amounts','03_merge_intervals','04_parse_csv','05_inventory_reserve','06_expire_sessions','07_json_patch','08_rate_limit','09_topological_sort','10_redact_secrets']
ARMS = ['baseline','solo-1.0']

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def tree_sha(path):
    h=hashlib.sha256()
    for f in sorted(x for x in path.rglob('*') if x.is_file()):
        h.update(str(f.relative_to(path)).replace('\\','/').encode()); h.update(b'\0'); h.update(f.read_bytes()); h.update(b'\0')
    return h.hexdigest()
def receipt(task, arm):
    root = REPAIR if task in {'05_inventory_reserve','07_json_patch'} else MATRIX
    p=root/f'{task}__{arm}__r1'/'receipt.json'
    return json.loads(p.read_text(encoding='utf-8'))

def compact(r):
    return r

selected=[receipt(t,a) for t in TASKS for a in ARMS]
assert len(selected)==20
assert {r['task_id'] for r in selected}==set(TASKS)
assert {r['arm'] for r in selected}==set(ARMS)
OUT.mkdir(parents=True, exist_ok=True)
with (OUT/'results.jsonl').open('w',encoding='utf-8') as f:
    for r in selected: f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')

by={(r['task_id'],r['arm']):r for r in selected}
pairs=[]
for task in TASKS:
    b=by[(task,'baseline')]; s=by[(task,'solo-1.0')]
    both_completed=all(r.get('status')=='completed' and (r.get('evaluation') or {}).get('task_success') for r in (b,s))
    pair_status='valid_completed_pair' if both_completed else ('censored' if any(r.get('failure_class') in {'capacity_error','timeout','codex_error'} for r in (b,s)) else ('failed' if any(r.get('status')=='failed' for r in (b,s)) else 'excluded'))
    pairs.append({'task_id':task,'baseline':{'status':b.get('status'),'failure_class':b.get('failure_class'),'quality_score':(b.get('evaluation') or {}).get('quality_score'),'elapsed_seconds':b.get('elapsed_seconds'),'total_tokens':((b.get('usage') or {}).get('usage') or {}).get('total_tokens'),'user_intervention_count':b.get('user_intervention_count'),'raw':b.get('raw')},'solo_1_0':{'status':s.get('status'),'failure_class':s.get('failure_class'),'quality_score':(s.get('evaluation') or {}).get('quality_score'),'elapsed_seconds':s.get('elapsed_seconds'),'total_tokens':((s.get('usage') or {}).get('usage') or {}).get('total_tokens'),'user_intervention_count':s.get('user_intervention_count'),'raw':s.get('raw')},'pair_status':pair_status,'comparison_eligible':both_completed})

def count_status(arm):
    vals=[r for r in selected if r['arm']==arm]
    return {'n':len(vals),'completed':sum(r.get('status')=='completed' for r in vals),'failed':sum(r.get('status')=='failed' for r in vals),'censored':sum(r.get('status')=='censored' for r in vals),'harness_error':sum(r.get('status')=='harness_error' for r in vals),'capacity_error':sum(r.get('failure_class')=='capacity_error' for r in vals),'user_intervention_total':sum(r.get('user_intervention_count',0) or 0 for r in vals),'token_observed_n':sum(bool((r.get('usage') or {}).get('usage')) for r in vals),'token_total_sum':sum((((r.get('usage') or {}).get('usage') or {}).get('total_tokens') or 0) for r in vals)}
summary={'schema_version':'ab-1.0-summary','generated_at':datetime.now(timezone.utc).isoformat(),'planned_trials':20,'selected_trials':20,'pairs':pairs,'pair_counts':{k:sum(x['pair_status']==k for x in pairs) for k in sorted({x['pair_status'] for x in pairs})},'arms':{a:count_status(a) for a in ARMS},'interpretation':'Only valid_completed_pair rows are eligible for quality/time/token deltas. capacity_error, timeout, codex_error and harness_error are preserved as non-comparable outcomes; this run is a reliability/observability pilot, not a statistically powered superiority claim.'}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

freeze={'schema_version':'ab-1.0-freeze','experiment_id':'ab-solo-1.0-2026-10-27','frozen_at':datetime.now(timezone.utc).isoformat(),'protocol':{'design':'10 matched task pairs, one fresh session and isolated copy per arm','planned_trials':20,'arms':ARMS,'repeats_per_task_arm':1,'model':'gpt-6.1-sol','reasoning_effort':'medium','timeout_seconds':300,'cli_version':'codex-cli 0.164.0-alpha.11.1','approval_policy':'never','sandbox':'dangerously-bypass-approvals-and-sandbox','service_tier':'default','memories':False,'plugins':False,'multi_agent':False,'network':'not used by prompt; host sandbox controls access'},'frozen_acceptance':{'task_success':'judge.py returns task_success=true and all frozen assertions pass','quality_score':'100 * passed / total; only completed trials are quality-comparable','public_inputs':'TASK.md, CONTRACT.md, and inputs.json hashes must remain unchanged','human_intervention':'count observable approval/user-input events; zero is reported as observed_zero_noninteractive','tokens':'extract latest thread_token_usage from disposable session JSONL; unavailable remains null','failure_semantics':{'capacity_error':'censored; never count as success','timeout':'censored','codex_error':'censored','task_failed':'failed','harness_error':'excluded and replaced only after harness correction'}},'tasks':{t:{'tree_sha256':tree_sha(TASKS_DIR/t)} for t in TASKS},'artifacts':{'judge_sha256':sha(OUT/'judge.py'),'runner_sha256':sha(EVAL/'ab-solo-1.0-2026-10-27-ab_runner.py'),'skill_sha256':sha(Path(r'C:\Users\Administrator\Documents\Codex\solo-dev-delivery-1.0-rebuild-v2\solo-dev-delivery')/'SKILL.md'),'package_sha256':sha(REPO/'packages'/'solo-dev-delivery-1.0.0.zip'),'raw_roots':[str(MATRIX),str(REPAIR),str(Path(r'C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-raw-v3')),str(Path(r'C:\Users\Administrator\Documents\Codex\ab-solo-1.0-2026-10-27-raw-v4'))]},'selected_trial_sources':{f'{t}__{a}__r1':str((REPAIR if t in {'05_inventory_reserve','07_json_patch'} else MATRIX)/f'{t}__{a}__r1') for t in TASKS for a in ARMS}}
(OUT/'experiment-freeze.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'selected':len(selected),'pair_counts':summary['pair_counts'],'arms':summary['arms']},ensure_ascii=False,indent=2))

