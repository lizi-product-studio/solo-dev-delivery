#!/usr/bin/env python3
"""Real-model domain smoke suite through the public Agent Runtime entry.

Three synthetic cases are acceptance samples, not a superiority benchmark.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'evaluations/runtime-domains-20261029/fixtures'


def run_task(task,output,model=None):
    root=output/task['id'];root.mkdir()
    workspace=root/'workspace'
    shutil.copytree(FIXTURES/task['workspace'],workspace)
    contract=FIXTURES/task['contract'];review=FIXTURES/task['review']
    (root/'contract.json').write_bytes(contract.read_bytes())
    frozen_review=root/'review.py';frozen_review.write_bytes(review.read_bytes())
    frozen={'task':task,'review_sha256':hashlib.sha256(review.read_bytes()).hexdigest(),
            'contract_sha256':hashlib.sha256(contract.read_bytes()).hexdigest(),
            'inputs':{p:hashlib.sha256((workspace/p).read_bytes()).hexdigest() for p in task['inputs']}}
    (root/'freeze.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf-8')
    prompt=task['goal']+' Read TASK.md and the required inputs with read_file. '
    prompt+='Only write the declared output files. Do not invent business facts; finish only after writing and reading back results.'
    argv=[sys.executable,'-X','utf8',str(ROOT/'portable-agent-kit/scripts/agent_run.py'),
          '--provider','codex','--goal',prompt,'--workspace',str(workspace),
          '--memory',str(root/'memory.sqlite3'),'--db',str(root/'control.sqlite3'),
          '--contract',str(root/'contract.json'),'--receipt',str(root/'receipt.json'),
          '--review-script',str(frozen_review),'--max-steps','16']
    if model:argv+=['--model',model]
    started=time.monotonic()
    try:
        proc=subprocess.run(argv,capture_output=True,encoding='utf-8',timeout=1200)
        (root/'stdout.log').write_text(proc.stdout,encoding='utf-8');(root/'stderr.log').write_text(proc.stderr,encoding='utf-8')
        report=json.loads((root/'receipt.json').read_text(encoding='utf-8'))
        read_paths={str(Path(e['decision']['arguments']['path']).as_posix()) for e in report['events']
                    if e['action']=='tool' and e['decision'].get('name')=='read_file' and 'result' in e}
        source_unchanged=all(hashlib.sha256((workspace/p).read_bytes()).hexdigest()==sha for p,sha in frozen['inputs'].items())
        reviews=report.get('independent_reviews',[])
        oracle_unchanged=hashlib.sha256(frozen_review.read_bytes()).hexdigest()==frozen['review_sha256']
        passed=(proc.returncode==0 and report['status']=='COMPLETED' and source_unchanged
                and oracle_unchanged and reviews and reviews[-1]['status']=='CHALLENGE_PASS' and set(task['inputs'])<=read_paths)
        result={'id':task['id'],'domain':task['domain'],'status':'PASS' if passed else 'FAIL',
                'runtime_status':report['status'],'steps':report['steps'],
                'first_review_pass':reviews[0]['status']=='CHALLENGE_PASS' if reviews else None,
                'review_attempts':len(reviews),'source_unchanged':source_unchanged,
                'oracle_unchanged':oracle_unchanged,
                'required_input_reads':set(task['inputs'])<=read_paths,'elapsed_seconds':round(time.monotonic()-started,3)}
    except Exception as exc:
        result={'id':task['id'],'domain':task['domain'],'status':'FAIL','error':type(exc).__name__+': '+str(exc)}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model');parser.add_argument('--task',action='append')
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((FIXTURES/'manifest.json').read_text(encoding='utf-8'))
    tasks=[task for task in manifest['tasks'] if not args.task or task['id'] in args.task]
    if not tasks:raise ValueError('no matching tasks')
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run_task,task,root,args.model) for task in tasks]
        for future in as_completed(futures):
            result=future.result();results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    summary={'status':'PASS' if all(r['status']=='PASS' for r in results) else 'INCOMPLETE',
             'tasks':sorted(results,key=lambda row:row['id']),'model_request':args.model or 'host default',
             'limits':manifest['limits'],'comparison':'none; acceptance smoke suite only'}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if summary['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
