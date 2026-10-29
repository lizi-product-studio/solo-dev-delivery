from __future__ import annotations
import argparse, concurrent.futures, json, os, subprocess, sys, time
from pathlib import Path

TASKS = [
    '01_normalize_email','02_sum_amounts','03_merge_intervals','04_parse_csv',
    '05_inventory_reserve','06_expire_sessions','07_json_patch',
    '08_rate_limit','09_topological_sort','10_redact_secrets'
]
ARMS = ['baseline','solo-1.0']


def run_one(args):
    task, arm, raw_root, runner = args
    label = f'{task}__{arm}__r1'
    target = raw_root / label
    if (target / 'receipt.json').exists():
        try:
            return json.loads((target / 'receipt.json').read_text(encoding='utf-8'))
        except Exception:
            pass
    raw_root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(runner), '--task', task, '--arm', arm, '--repeat', '1', '--output', str(raw_root)]
    log = raw_root / f'{label}.orchestration.log'
    started = time.time()
    with log.open('w', encoding='utf-8') as stream:
        proc = subprocess.run(cmd, cwd=str(runner.parent.parent), capture_output=True, text=True, encoding='utf-8', errors='replace')
        stream.write(proc.stdout)
        if proc.stderr:
            stream.write('\n[stderr]\n' + proc.stderr)
    receipt = None
    if (target / 'receipt.json').exists():
        try:
            receipt = json.loads((target / 'receipt.json').read_text(encoding='utf-8'))
        except Exception as exc:
            receipt = {'status':'harness_error','trial_id':label,'task_id':task,'arm':arm,'error':repr(exc)}
    if receipt is None:
        receipt = {'status':'harness_error','trial_id':label,'task_id':task,'arm':arm,'exit_code':proc.returncode,'stdout':proc.stdout[-2000:],'stderr':proc.stderr[-2000:]}
    receipt['_orchestration_seconds'] = round(time.time() - started, 3)
    receipt['_orchestration_log'] = str(log)
    return receipt


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--raw-root', type=Path, required=True)
    p.add_argument('--runner', type=Path, default=Path(__file__).with_name('ab-solo-1.0-2026-10-27-ab_runner.py'))
    p.add_argument('--workers', type=int, default=2)
    args = p.parse_args()
    raw = args.raw_root.resolve(); runner = args.runner.resolve()
    jobs = [(task, arm, raw, runner) for task in TASKS for arm in ARMS]
    results_path = raw / 'matrix-progress.jsonl'; raw.mkdir(parents=True, exist_ok=True)
    existing = {}
    if results_path.exists():
        for line in results_path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                item=json.loads(line); existing[item.get('trial_id')] = item
            except Exception:
                pass
    jobs = [job for job in jobs if f'{job[0]}__{job[1]}__r1' not in existing]
    with results_path.open('a', encoding='utf-8') as out:
        if not jobs:
            return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool:
            futures = {pool.submit(run_one, job): job for job in jobs}
            for future in concurrent.futures.as_completed(futures):
                job = futures[future]
                try: result = future.result()
                except Exception as exc: result={'status':'harness_error','trial_id':f'{job[0]}__{job[1]}__r1','task_id':job[0],'arm':job[1],'error':repr(exc)}
                out.write(json.dumps(result, ensure_ascii=False) + '\n'); out.flush()
                print(json.dumps({'trial_id':result.get('trial_id'),'status':result.get('status'),'failure_class':result.get('failure_class'),'quality':(result.get('evaluation') or {}).get('quality_score')}, ensure_ascii=False), flush=True)
    return 0

if __name__ == '__main__': raise SystemExit(main())
