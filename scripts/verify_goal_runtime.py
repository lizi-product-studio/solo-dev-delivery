#!/usr/bin/env python3
"""Real-model goal intake acceptance; no prewritten execution contract.

The fixed synthetic cases and host reviewer are acceptance samples, not a
general-intelligence or framework-superiority benchmark.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from verify_domain_runtime import ROOT, FIXTURES


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(task, output, model, effort, max_steps):
    root = output / task['id']; root.mkdir()
    workspace = root / 'workspace'
    shutil.copytree(FIXTURES / task['workspace'], workspace)
    review = root / 'review.py'; review.write_bytes((FIXTURES / task['review']).read_bytes())
    goal = (FIXTURES / task['goal_path']).read_text(encoding='utf-8')
    hashes = {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in task['inputs']}
    write(root / 'freeze.json', {'goal': goal, 'goal_sha256': hashlib.sha256(goal.encode()).hexdigest(),
                                'input_hashes': hashes, 'review_sha256': hashlib.sha256(review.read_bytes()).hexdigest()})
    argv = [sys.executable, '-X', 'utf8', str(ROOT / 'portable-agent-kit/scripts/agent_task.py'),
            '--goal', goal, '--workspace', str(workspace), '--control-root', str(root / 'control'),
            '--project-store',str(root / 'project-store'),
            '--authorization-ref', 'user-authorized-synthetic-local-acceptance',
            '--review-script', str(review), '--model', model, '--effort', effort,
            '--isolate-model-config', '--max-steps', str(max_steps)]
    for name in task['outputs']: argv += ['--allow-write', name]
    start = time.monotonic()
    process = subprocess.run(argv, capture_output=True, encoding='utf-8', timeout=1200)
    (root / 'stdout.log').write_text(process.stdout, encoding='utf-8')
    (root / 'stderr.log').write_text(process.stderr, encoding='utf-8')
    receipt = json.loads((root / 'control/intake-receipt.json').read_text(encoding='utf-8'))
    runtime_path = root / 'control/receipt.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.exists() else {}
    unchanged = all(hashlib.sha256((workspace / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    goal_bound = receipt['original_goal_sha256'] == hashlib.sha256(goal.encode()).hexdigest()
    reads = {e['decision'].get('arguments', {}).get('path') for e in runtime.get('events', [])
             if e['action'] == 'tool' and e['decision'].get('name') in {'read_file', 'read_json'}
             and not (isinstance(e.get('result'), dict) and e['result'].get('status') == 'FAILED')}
    reviews = runtime.get('independent_reviews', [])
    passed = (process.returncode == 0 and receipt['status'] == 'COMPLETED' and unchanged and goal_bound
              and set(task['inputs']) <= reads and reviews and reviews[-1]['status'] == 'CHALLENGE_PASS'
              and receipt['total_decision_steps'] <= max_steps)
    result = {'task': task['id'], 'status': 'PASS' if passed else 'FAIL', 'runtime_status': receipt['status'],
              'planning_steps': receipt['planning']['decision_steps'], 'execution_steps': receipt.get('execution_steps'),
              'total_decision_steps': receipt.get('total_decision_steps'), 'review_attempts': len(reviews),
              'original_goal_bound': goal_bound, 'inputs_unchanged': unchanged,
              'required_inputs_read': set(task['inputs']) <= reads, 'elapsed_seconds': round(time.monotonic() - start, 3),
              'limits': 'Real model on fixed synthetic inputs. No raw-model comparison or production proof.'}
    write(root / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True); parser.add_argument('--effort', default='medium')
    parser.add_argument('--max-steps', type=int, default=16); parser.add_argument('--task', action='append')
    args = parser.parse_args(); root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    tasks = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))['tasks']
    if args.task:
        if set(args.task) - {task['id'] for task in tasks}: parser.error('unknown task')
        tasks = [task for task in tasks if task['id'] in args.task]
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for folder in ('agent_runtime', 'control_plane', 'scripts', 'core')
               for p in (ROOT / 'portable-agent-kit' / folder).glob('*.py')}
    write(root / 'protocol.json', {'format': 'goal-runtime-acceptance/1', 'model': args.model,
                                  'effort': args.effort, 'max_steps': args.max_steps, 'source_hashes': sources,
                                  'prewritten_contract': False, 'tasks': [task['id'] for task in tasks]})
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, task, root, args.model, args.effort, args.max_steps) for task in tasks]
        for future in as_completed(futures):
            result = future.result(); results.append(result); print(json.dumps(result), flush=True)
    stable = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in sources.items())
    summary = {'status': 'PASS' if stable and all(r['status'] == 'PASS' for r in results) else 'INCOMPLETE',
               'source_unchanged': stable, 'results': results,
               'limits': 'Three fixed synthetic cases; no general weak-model improvement claim.'}
    write(root / 'summary.json', summary)
    return 0 if summary['status'] == 'PASS' else 2


if __name__ == '__main__': raise SystemExit(main())
