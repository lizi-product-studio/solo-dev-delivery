#!/usr/bin/env python3
"""Explicit bounded queue worker for Agent Runtime (no daemon or timer)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_runtime.scheduler import TaskScheduler
from agent_run import main as run_agent


def validate_job(job):
    if not isinstance(job,dict):raise ValueError('job must be an object')
    required={'goal','workspace','contract'}
    allowed=required|{'provider','model','max_steps','project_id'}
    if set(job)-allowed or not required<=set(job):raise ValueError('invalid job fields')
    if any(not isinstance(job[key],str) or not job[key].strip() for key in required):
        raise ValueError('job goal, workspace and contract must be strings')
    if job.get('provider','codex') not in {'codex','api'}:raise ValueError('unsupported provider')
    if type(job.get('max_steps',12))!=int or not 1<=job.get('max_steps',12)<=1000:
        raise ValueError('max_steps must be 1..1000')
    return job


def worker_handler(control_root):
    root=Path(control_root).resolve();root.mkdir(parents=True,exist_ok=True)
    def handle(claimed):
        job=validate_job(claimed['payload'])
        slug=hashlib.sha256(claimed['task_id'].encode('utf-8')).hexdigest()[:24]
        directory=root/slug
        directory.mkdir(exist_ok=False)
        contract=Path(job['contract']).resolve()
        (directory/'approved-contract.json').write_bytes(contract.read_bytes())
        receipt=directory/'receipt.json'
        argv=['--goal',job['goal'],'--workspace',str(Path(job['workspace']).resolve()),
              '--memory',str(root/'memory.sqlite3'),'--db',str(directory/'control.sqlite3'),
              '--contract',str(directory/'approved-contract.json'),'--receipt',str(receipt),
              '--task-id',claimed['task_id'],'--provider',job.get('provider','codex'),
              '--max-steps',str(job.get('max_steps',12))]
        if job.get('model'):argv+=['--model',job['model']]
        if job.get('project_id'):argv+=['--project-id',job['project_id']]
        run_agent(argv)
        value=json.loads(receipt.read_text(encoding='utf-8'))
        return {'status':value['status'],'receipt':str(receipt),
                'receipt_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'runtime_task_id':value['task_id']}
    return handle


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    add=sub.add_parser('enqueue');add.add_argument('--task-id',required=True);add.add_argument('--job',type=Path,required=True)
    add.add_argument('--depends-on',action='append',default=[])
    work=sub.add_parser('work');work.add_argument('--worker',required=True);work.add_argument('--control-root',type=Path,required=True)
    work.add_argument('--max-tasks',type=int,default=1)
    sub.add_parser('list')
    args=parser.parse_args(argv);queue=TaskScheduler(args.queue)
    if args.command=='enqueue':
        job=validate_job(json.loads(args.job.read_text(encoding='utf-8-sig')))
        # Persist absolute locations so restarting the worker from another cwd
        # does not change the authorized workspace/contract.
        job={**job,'workspace':str(Path(job['workspace']).resolve()),'contract':str(Path(job['contract']).resolve())}
        result=queue.enqueue(args.task_id,job,args.depends_on)
    elif args.command=='work':result=queue.run_batch(args.worker,worker_handler(args.control_root),max_tasks=args.max_tasks)
    else:result=queue.list_tasks()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
