"""Management commands for the existing managed task, without a second executor."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_runtime.journal import JournalError, RunJournal
from agent_runtime.task_lifecycle import (LifecycleError,load_task,task_status,
    resume_arguments,reconcile)
from agent_run import main as run_agent
from agent_task import save


def _execute(root, argv, operation):
    """Archive earlier receipts; a failed ownership claim never rewrites them."""
    task=load_task(root)
    operation_id=uuid.uuid4().hex
    directory=root/'operations'/operation_id
    directory.mkdir(parents=True,exist_ok=False)
    receipt=root/'receipt.json'
    if receipt.exists():(directory/'previous-runtime.json').write_bytes(receipt.read_bytes())
    started=time.monotonic()
    record={'format':'managed-task-operation/1','operation_id':operation_id,
            'task_id':task['task_id'],'operation':operation,'status':'RUNNING',
            'requested_at':datetime.now(timezone.utc).isoformat(),'runtime_argv':argv}
    save(directory/'operation.json',record)
    captured=io.StringIO()
    own_runtime={}
    def own_report(runtime):
        own_runtime.update(runtime)
        intake=json.loads((root/'intake-receipt.json').read_text(encoding='utf-8'))
        intake.update(status=runtime['status'],execution_steps=runtime['steps'],
                      semantic_review_attempts=runtime.get('semantic_review_attempts',0),
                      total_decision_steps=intake['planning']['decision_steps']+intake.get('review_preparation_steps',0)+runtime['steps']+runtime.get('semantic_review_attempts',0),
                      last_operation_id=operation_id,invocation_id=runtime['invocation_id'],
                      invocation_receipt=runtime['invocation_receipt'])
        return intake
    try:
        with redirect_stdout(captured):run_agent(argv,report_callback=own_report)
        record.update(status=own_runtime['status'],execution_steps=own_runtime['steps'],
                      invocation_id=own_runtime['invocation_id'],invocation_receipt=own_runtime['invocation_receipt'])
    except Exception as exc:
        record.update(status='PUBLICATION_FAILED' if own_runtime else 'OPERATION_REJECTED',
                      execution_status=own_runtime.get('status'),error=type(exc).__name__+': '+str(exc))
    finally:
        (directory/'stdout.log').write_text(captured.getvalue(),encoding='utf-8')
        record['elapsed_seconds']=round(time.monotonic()-started,3)
        save(directory/'operation.json',record)
    return {**record,'observed':task_status(root),'operation_receipt':str(directory/'operation.json')}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('status','resume','pause','cancel','reconcile'))
    parser.add_argument('--control-root',type=Path,required=True)
    parser.add_argument('--steps-per-run',type=int)
    parser.add_argument('--reason')
    parser.add_argument('--request-id',help='idempotency key for pause/cancel')
    parser.add_argument('--action-id')
    parser.add_argument('--evidence',type=Path,help='optional saved readback reference; the host revalidates it')
    parser.add_argument('--reviewer-id',default='managed-file-readback-host')
    args=parser.parse_args(argv)
    if args.steps_per_run is not None and (args.operation!='resume' or args.steps_per_run<1):
        parser.error('--steps-per-run is a positive resume chunk size')
    if args.operation in {'pause','cancel'} and not (args.reason and args.reason.strip()):
        parser.error('--reason is required for a control request')
    if args.operation=='reconcile' and not args.action_id:
        parser.error('--action-id is required; status reports the pending identity')
    root=args.control_root.resolve()
    try:
        if args.operation=='status':
            result=task_status(root)
        elif args.operation=='resume':
            result=_execute(root,resume_arguments(root,args.steps_per_run),'resume')
        elif args.operation in {'pause','cancel'}:
            task=load_task(root);observed=task_status(root)
            if args.operation=='cancel' and observed.get('status')=='CANCELLED':
                result={**observed,'status':'CANCELLED','request_needed':False}
            elif args.operation=='pause' and observed.get('phase')=='PAUSED':
                result={**observed,'status':'PAUSED','request_needed':False}
            else:
                if not Path(task['state_db']).is_file():
                    raise LifecycleError('NO_CONTROL_CHECKPOINT: executor has not started; no request was accepted')
                journal=RunJournal(task['state_db'])
                request=journal.request_control(task['task_id'],args.operation,args.reason,request_id=args.request_id)
                result={'status':args.operation.upper()+'_REQUESTED','request':request,
                        'observed':task_status(root)}
                if args.operation=='cancel':
                    try:cancel_argv=resume_arguments(root,for_cancel=True)
                    except LifecycleError:cancel_argv=None
                    if cancel_argv is not None:
                        result=_execute(root,cancel_argv,'acknowledge-cancel')
        else:
            evidence=json.loads(args.evidence.read_text(encoding='utf-8-sig')) if args.evidence else None
            result=reconcile(root,args.action_id,evidence,args.reviewer_id)
            observed=task_status(root)
            if observed.get('pending_control',{} ) and observed['pending_control'].get('operation')=='cancel':
                result={'reconciliation':result,**_execute(root,resume_arguments(root,for_cancel=True),'acknowledge-cancel')}
            else:result={**result,'observed':observed}
    except (LifecycleError,JournalError,OSError,ValueError,KeyError) as exc:
        result={'status':'OPERATION_REJECTED','error':type(exc).__name__+': '+str(exc)}
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 2 if result.get('status') in {'OPERATION_REJECTED','NEEDS_RECONCILIATION'} else 0


if __name__=='__main__':raise SystemExit(main())
