#!/usr/bin/env python3
"""Explicit bounded queue worker for Agent Runtime (no daemon or timer)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import os
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_runtime.scheduler import TaskScheduler
from agent_run import main as run_agent


def validate_job(job):
    if not isinstance(job,dict):raise ValueError('job must be an object')
    required={'goal','workspace','contract'}
    allowed=required|{'provider','model','max_steps','project_id','effort',
                      'isolate_model_config','review_script','auto_verify'}
    if set(job)-allowed or not required<=set(job):raise ValueError('invalid job fields')
    if any(not isinstance(job[key],str) or not job[key].strip() for key in required):
        raise ValueError('job goal, workspace and contract must be strings')
    provider=job.get('provider','codex')
    if provider not in ('codex','api'):raise ValueError('unsupported provider')
    for key in ('model','project_id','review_script'):
        if key in job and (not isinstance(job[key],str) or not job[key].strip()):
            raise ValueError(key+' must be a nonempty string')
    for key in ('isolate_model_config','auto_verify'):
        if key in job and type(job[key]) is not bool:raise ValueError(key+' must be boolean')
    if 'effort' in job:
        if not isinstance(job['effort'],str) or job['effort'] not in ('low','medium','high','xhigh','max','ultra'):
            raise ValueError('unsupported reasoning effort')
        if provider!='codex':raise ValueError('effort is supported only with provider codex')
    if job.get('isolate_model_config') and (provider!='codex' or not job.get('model')):
        raise ValueError('isolate_model_config requires provider codex and an explicit model')
    if job.get('auto_verify') and not job.get('review_script'):
        raise ValueError('auto_verify requires review_script')
    if type(job.get('max_steps',12))!=int or not 1<=job.get('max_steps',12)<=1000:
        raise ValueError('max_steps must be 1..1000')
    return job


def worker_handler(control_root):
    root=Path(control_root).resolve()
    def handle(claimed):
        job=validate_job(claimed['payload'])
        workspace=Path(job['workspace']).resolve()
        slug=hashlib.sha256(claimed['task_id'].encode('utf-8')).hexdigest()[:24]
        directory=root/slug
        if root.is_relative_to(workspace) or directory.is_relative_to(workspace):
            raise ValueError('queue control files must stay outside the tool workspace')
        contract=Path(job['contract']).resolve()
        contract_bytes=contract.read_bytes()
        approved=json.loads(contract_bytes.decode('utf-8-sig'))
        if not isinstance(approved,dict):raise ValueError('contract must be an object')
        review_bytes=None
        if job.get('review_script'):
            source_review=Path(job['review_script']).resolve(strict=True)
            if source_review.is_relative_to(workspace):
                raise ValueError('trusted review script must be outside the tool workspace')
            review_bytes=source_review.read_bytes()
            review_hash=hashlib.sha256(review_bytes).hexdigest()
            if 'required_review_sha256' in approved and approved['required_review_sha256']!=review_hash:
                raise ValueError('required_review_sha256 does not match the frozen reviewer')
            if 'required_review_sha256' not in approved:
                approved['required_review_sha256']=review_hash
                contract_bytes=(json.dumps(approved,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        elif 'required_review_sha256' in approved:
            raise ValueError('contract requires its bound review_script')
        root.mkdir(parents=True,exist_ok=True)
        directory.mkdir(exist_ok=False)
        (directory/'approved-contract.json').write_bytes(contract_bytes)
        review_path=None
        if review_bytes is not None:
            review_path=directory/'trusted-review.py'
            review_path.write_bytes(review_bytes)
        receipt=directory/'receipt.json'
        argv=['--goal',job['goal'],'--workspace',str(workspace),
              '--memory',str(root/'memory.sqlite3'),'--db',str(directory/'control.sqlite3'),
              '--contract',str(directory/'approved-contract.json'),'--receipt',str(receipt),
              '--task-id',claimed['task_id'],'--provider',job.get('provider','codex'),
              '--max-steps',str(job.get('max_steps',12))]
        if job.get('model'):argv+=['--model',job['model']]
        if job.get('project_id'):argv+=['--project-id',job['project_id']]
        if job.get('effort'):argv+=['--effort',job['effort']]
        if job.get('isolate_model_config'):argv+=['--isolate-model-config']
        if review_path is not None:argv+=['--review-script',str(review_path)]
        if job.get('auto_verify'):argv+=['--auto-verify']
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
    work.add_argument('--watch',action='store_true',help='wait for newly queued tasks between jobs')
    work.add_argument('--max-seconds',type=float,default=3600)
    work.add_argument('--idle-seconds',type=float,default=1)
    work.add_argument('--clear-stop',action='store_true',help='explicitly clear a previous worker stop request')
    stop=sub.add_parser('stop');stop.add_argument('--control-root',type=Path,required=True)
    sub.add_parser('list')
    args=parser.parse_args(argv);queue=TaskScheduler(args.queue)
    if args.command=='enqueue':
        job=validate_job(json.loads(args.job.read_text(encoding='utf-8-sig')))
        # Persist absolute locations so restarting the worker from another cwd
        # does not change the authorized workspace/contract.
        job={**job,'workspace':str(Path(job['workspace']).resolve()),'contract':str(Path(job['contract']).resolve())}
        if job.get('review_script'):job['review_script']=str(Path(job['review_script']).resolve())
        result=queue.enqueue(args.task_id,job,args.depends_on)
    elif args.command=='stop':
        directory=args.control_root.resolve();directory.mkdir(parents=True,exist_ok=True)
        (directory/'worker.stop').write_text('stop requested\n',encoding='utf-8')
        result={'status':'STOP_REQUESTED','effect':'worker exits between jobs; current task retains its own controls'}
    elif args.command=='work':
        if not 1<=args.max_tasks<=1000 or not 0<args.max_seconds<=86400 or not 0<args.idle_seconds<=60:
            parser.error('worker limits are out of bounds')
        if not args.watch:result=queue.run_batch(args.worker,worker_handler(args.control_root),max_tasks=args.max_tasks)
        else:
            directory=args.control_root.resolve();directory.mkdir(parents=True,exist_ok=True)
            state_path=directory/'worker-state.json';stop_path=directory/'worker.stop'
            lock_path=directory/'worker.lock';identity=uuid.uuid4().hex
            try:
                with lock_path.open('x',encoding='utf-8') as lock:lock.write(identity)
            except FileExistsError:parser.error('watch worker already registered; inspect the existing worker before recovery')
            if args.clear_stop and stop_path.is_file():stop_path.unlink()
            started=time.monotonic();results=[];handler=worker_handler(directory)
            def publish(status):
                temporary=state_path.with_name(state_path.name+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_text(json.dumps({'worker':args.worker,'pid':os.getpid(),'status':status,
                    'completed_jobs':len(results),'elapsed_seconds':time.monotonic()-started}),encoding='utf-8')
                os.replace(temporary,state_path)
            publish('RUNNING')
            try:
                while len(results)<args.max_tasks and time.monotonic()-started<args.max_seconds and not stop_path.exists():
                    batch=queue.run_batch(args.worker,handler,max_tasks=1)
                    results.extend(batch);publish('RUNNING' if batch else 'IDLE')
                    if batch and batch[-1]['status']=='UNKNOWN':break
                    if not batch:time.sleep(min(args.idle_seconds,max(0,args.max_seconds-(time.monotonic()-started))))
            finally:
                publish('STOPPED')
                if lock_path.read_text(encoding='utf-8')==identity:lock_path.unlink()
            result={'status':'STOPPED','results':results,'state':str(state_path)}
    else:result=queue.list_tasks()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
