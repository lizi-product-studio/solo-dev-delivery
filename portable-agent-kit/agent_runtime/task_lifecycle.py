"""Host coordination for sealed, managed tasks; never executes a saved command.

Control files are trusted host state outside the tool workspace. Hashes detect
drift, not host identity. Status is read-only; file reconciliation observes exact
pending write intent and updates the two state records in one SQLite transaction.
"""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from control_plane.contracts import canonical_hash, normalize_contract
from control_plane.evaluator import make_verifier, verify_evidence
from core.state_machine import apply as apply_event


class LifecycleError(ValueError):
    pass


STATIC_FILES = ('request.json', 'plan.json', 'contract.json', 'trusted-review.py', 'runtime-arguments.json')
VALUE_FLAGS = {'--goal', '--workspace', '--memory', '--skills', '--project-id', '--db', '--state-db',
               '--contract', '--receipt', '--task-id', '--review-script', '--max-steps', '--provider',
               '--model', '--effort', '--steps-per-run'}
BOOL_FLAGS = {'--auto-verify', '--isolate-model-config'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _regular(path, *, exists=True):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise LifecycleError('control path must not contain links')
    if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
        raise LifecycleError('control path must be a regular, unaliased file')
    if exists and not path.is_file():
        raise LifecycleError('missing control file: ' + path.name)
    return path.resolve()


def _read(path):
    path = _regular(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise LifecycleError('control file exceeds 2 MiB')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _root(value):
    root = Path(value).absolute()
    for item in (root, *root.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise LifecycleError('control root must not contain links')
    if not root.is_dir():
        raise LifecycleError('control root does not exist')
    return root.resolve()


def _options(argv):
    if not isinstance(argv, list) or len(argv) > 64 or any(not isinstance(x, str) for x in argv):
        raise LifecycleError('invalid saved Runtime arguments')
    result = {}; index = 0
    while index < len(argv):
        key = argv[index]
        if key in result or key not in VALUE_FLAGS | BOOL_FLAGS:
            raise LifecycleError('duplicate or unsupported saved Runtime option')
        if key in BOOL_FLAGS:
            result[key] = True; index += 1
        else:
            if index + 1 >= len(argv): raise LifecycleError('missing saved Runtime option value')
            result[key] = argv[index + 1]; index += 2
    return result


def _bound(root, argv):
    args = _options(argv); request = _read(root / 'request.json'); contract = _read(root / 'contract.json')
    if not isinstance(request,dict) or not isinstance(contract,dict):
        raise LifecycleError('request and contract must be JSON objects')
    required = {'--goal', '--workspace', '--memory', '--skills', '--project-id', '--db', '--contract',
                '--receipt', '--task-id', '--review-script', '--max-steps', '--auto-verify'}
    if not required <= set(args): raise LifecycleError('managed task is missing required Runtime options')
    workspace = Path(request['workspace']).resolve(strict=True)
    if root.is_relative_to(workspace): raise LifecycleError('control root must remain outside workspace')
    if args['--goal'] != request['goal'] or args['--goal'].strip() != contract['goal']:
        raise LifecycleError('original goal binding mismatch')
    if _sha(args['--goal'].encode('utf-8')) != request['goal_sha256']:
        raise LifecycleError('original goal hash mismatch')
    if Path(args['--workspace']).resolve() != workspace or args['--task-id'] != contract['task_id']:
        raise LifecycleError('task/workspace binding mismatch')
    for option, name in {'--db':'control.sqlite3', '--state-db':'control.sqlite3', '--contract':'contract.json',
                         '--receipt':'receipt.json', '--review-script':'trusted-review.py'}.items():
        if option in args and Path(args[option]).resolve() != root / name:
            raise LifecycleError('managed control path mismatch: ' + option)
    for option in ('--memory', '--skills'):
        target = _regular(args[option], exists=False)
        if target.is_relative_to(workspace): raise LifecycleError('learning database is inside tool workspace')
    if Path(args['--memory']).resolve() == Path(args['--skills']).resolve():
        raise LifecycleError('memory and skills databases must differ')
    intake = contract.get('intake', {})
    if intake.get('format') != 'goal-intake/1' or intake.get('goal_sha256') != request['goal_sha256']:
        raise LifecycleError('missing managed intake binding')
    if int(args['--max-steps']) != intake['execution_decision_budget']:
        raise LifecycleError('model budget binding mismatch')
    if contract.get('required_review_sha256') != _sha((root / 'trusted-review.py').read_bytes()):
        raise LifecycleError('independent reviewer binding mismatch')
    if canonical_hash(_read(root / 'plan.json')) != intake['plan_sha256']:
        raise LifecycleError('planning binding mismatch')
    return {'task_id':args['--task-id'], 'workspace':str(workspace), 'state_db':str(root / 'control.sqlite3'),
            'runtime_argv':list(argv), 'control_root':str(root), 'request':request, 'contract':contract}


def seal_task(control_root, runtime_argv=None):
    root = _root(control_root)
    argv = _read(root / 'runtime-arguments.json')
    if runtime_argv is not None and argv != runtime_argv:
        raise LifecycleError('provided arguments differ from saved arguments')
    value = _bound(root, argv); report = _read(root / 'intake-receipt.json')
    if not isinstance(report,dict):raise LifecycleError('intake receipt must be an object')
    if report['task_id'] != value['task_id']:
        raise LifecycleError('intake task mismatch')
    if report['request_sha256'] != _sha((root / 'request.json').read_bytes()):
        raise LifecycleError('intake request hash mismatch')
    if report['plan_sha256'] != _sha((root / 'plan.json').read_bytes()):
        raise LifecycleError('intake plan hash mismatch')
    manifest = {'format':'managed-task/1', 'task_id':value['task_id'], 'workspace':value['workspace'],
                'state_db':value['state_db'], 'control_root':str(root),
                'static_files':{name:_sha(_regular(root / name).read_bytes()) for name in STATIC_FILES},
                'limits':'Host-owned control state; hashes do not authenticate host identity.'}
    destination = root / 'managed-task.json'
    if destination.exists():
        if _read(destination) != manifest: raise LifecycleError('existing managed manifest differs')
    else:
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2); stream.write('\n')
    value['manifest'] = manifest
    return value


def load_task(control_root):
    root = _root(control_root); manifest = _read(root / 'managed-task.json')
    if not isinstance(manifest,dict) or manifest.get('format') != 'managed-task/1' or set(manifest.get('static_files', {})) != set(STATIC_FILES):
        raise LifecycleError('unsupported managed task manifest')
    for name, digest in manifest['static_files'].items():
        if _sha(_regular(root / name).read_bytes()) != digest:
            raise LifecycleError('STALE_MANAGED_TASK: ' + name)
    value = _bound(root, _read(root / 'runtime-arguments.json'))
    if any(manifest.get(key) != value[key] for key in ('task_id','workspace','state_db','control_root')):
        raise LifecycleError('managed manifest path/task mismatch')
    value['manifest'] = manifest
    return value


def _records(db, task):
    row = db.execute('SELECT state_json,contract_json FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()
    if row is None: raise LifecycleError('managed task has no control record')
    state, contract = json.loads(row[0]), json.loads(row[1])
    if canonical_hash(normalize_contract(task['contract'])) != state['contract_hash'] or contract != normalize_contract(task['contract']):
        raise LifecycleError('managed contract differs from persisted task')
    has_journal=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_checkpoints'").fetchone()
    checkpoint = db.execute('SELECT binding,generation,snapshot FROM agent_checkpoints WHERE task_id=?', (task['task_id'],)).fetchone() if has_journal else None
    lease = db.execute('SELECT lease_id,owner,expires_at FROM leases WHERE task_id=?', (task['task_id'],)).fetchone()
    return state, (dict(binding=checkpoint[0], generation=checkpoint[1], snapshot=json.loads(checkpoint[2])) if checkpoint else None), lease


def task_status(control_root):
    root = _root(control_root)
    if not (root / 'managed-task.json').exists():
        report = _read(root / 'intake-receipt.json')
        return {'format':'managed-task-status/1','task_id':report['task_id'],'status':report['status'],
                'phase':None,'can_resume':False,'execution_verified':False,'control_requests':[],
                'pending_control':None,'lease':None,'next_action':'WAIT_FOR_SEALED_EXECUTION'}
    task = load_task(root); database = _regular(task['state_db'], exists=False)
    if not database.exists():
        return {'format':'managed-task-status/1','task_id':task['task_id'],'status':'NOT_STARTED',
                'phase':None,'can_resume':False,'control_requests':[],'pending_control':None,
                'lease':None,'next_action':'WAIT_FOR_EXECUTOR'}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        state, saved, lease = _records(db, task)
        has_controls=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_control_requests'").fetchone()
        controls=[]
        if has_controls:
            for row in db.execute('SELECT sequence,request_id,operation,reason,status,created_at,acknowledged_at,result FROM agent_control_requests WHERE task_id=? ORDER BY sequence',(task['task_id'],)):
                item=dict(zip(('sequence','request_id','operation','reason','status','created_at','acknowledged_at','result'),row))
                if item['result'] is not None:item['result']=json.loads(item['result'])
                controls.append(item)
    pending_controls=sorted((row for row in controls if row['status']=='REQUESTED'),key=lambda row:(row['operation']!='cancel',row['sequence']))
    snapshot = saved['snapshot'] if saved else {}
    pending = snapshot.get('phase') in {'MODEL_PENDING','ACTION_PENDING','FINISH_PENDING'} or bool(snapshot.get('pending'))
    terminal = state['status'] in {'COMPLETED','CANCELLED','FAILED'} or snapshot.get('phase') in {'FINISHED','CANCELLED'}
    review_exhausted = snapshot.get('rejections',0) > state['contract']['budget']['max_retries']
    can_resume = (bool(saved) and not pending and not terminal and not lease and not review_exhausted
                  and not state.get('unknown_tool_calls') and not state.get('failed_tool_calls')
                  and snapshot.get('phase') in {'READY','PAUSED'})
    return {'format':'managed-task-status/1','task_id':task['task_id'], 'status':state['status'],
            'phase':snapshot.get('phase'), 'last_status':snapshot.get('last_status'),
            'next_step':snapshot.get('next_step'), 'pending':deepcopy(snapshot.get('pending')),
            'unknown_tool_calls':deepcopy(state.get('unknown_tool_calls', [])),
            'failed_tool_calls':deepcopy(state.get('failed_tool_calls', [])),
            'lease':({'lease_id':lease[0], 'owner':lease[1], 'expires_at':lease[2]} if lease else None),
            'can_resume':can_resume, 'review_budget_exhausted':review_exhausted,
            'control_requests':controls, 'pending_control':pending_controls[0] if pending_controls else None,
            'next_action':'RECONCILE_OR_VERIFY_EXECUTOR_STOP' if pending else ('RESUME' if can_resume else ('NONE' if terminal else 'WAIT_OR_RECOVER_OWNERSHIP'))}


def resume_arguments(control_root, steps_per_run=None, *, for_cancel=False):
    task = load_task(control_root); observed = task_status(control_root)
    if type(for_cancel) is not bool:raise LifecycleError('for_cancel must be boolean')
    cancel_ack=(for_cancel and (observed.get('pending_control') or {}).get('operation')=='cancel'
                and observed.get('phase') in {'READY','PAUSED'} and not observed.get('pending')
                and not observed.get('unknown_tool_calls')
                and observed.get('lease') is None and observed['status'] not in {'COMPLETED','CANCELLED','FAILED'})
    if for_cancel and not cancel_ack:
        raise LifecycleError('cancel acknowledgement requires a persisted cancel request at a safe boundary')
    if not observed['can_resume'] and not cancel_ack:
        raise LifecycleError('RESUME_BLOCKED: ' + observed['next_action'])
    if steps_per_run is not None and (type(steps_per_run) is not int or steps_per_run < 1):
        raise LifecycleError('steps_per_run must be positive')
    argv = list(task['runtime_argv'])
    if '--steps-per-run' in argv:
        index = argv.index('--steps-per-run'); del argv[index:index+2]
    if steps_per_run is not None: argv += ['--steps-per-run',str(steps_per_run)]
    return argv + ['--resume']


def _intent(task, saved, action_id):
    snapshot = saved['snapshot']; pending = snapshot.get('pending')
    if snapshot.get('phase') != 'ACTION_PENDING' or not pending or pending.get('id') != action_id:
        raise LifecycleError('no matching unknown action')
    if snapshot.get('last_status') != 'NEEDS_RECONCILIATION':
        raise LifecycleError('NEEDS_EXECUTOR_STOP: pending action has no safe-stop acknowledgement')
    decision = pending['decision']; arguments = decision.get('arguments', {})
    if decision.get('action') != 'tool' or decision.get('name') not in {'write_file','write_json'}:
        raise LifecycleError('NEEDS_ADAPTER: exact original write bytes are unavailable')
    name = arguments.get('path')
    if not isinstance(name,str) or not name or ':' in name or '..' in Path(name.replace('\\','/')).parts or Path(name).is_absolute():
        raise LifecycleError('pending write path is not workspace relative')
    workspace = Path(task['workspace']); path = workspace / name
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)):
        raise LifecycleError('pending readback target must not contain links')
    target = path.resolve()
    if not target.is_relative_to(workspace): raise LifecycleError('pending target escapes workspace')
    relative = target.relative_to(workspace).as_posix()
    if not any(relative == scope or (scope.endswith('/') and relative.startswith(scope)) for scope in task['contract']['scope']):
        raise LifecycleError('pending target is outside original scope')
    try:
        text = (str(arguments['text']) if decision['name'] == 'write_file'
                else json.dumps(arguments['value'], ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        expected = text.encode('utf-8')
    except (KeyError, ValueError, TypeError, UnicodeError) as exc:
        raise LifecycleError('original write bytes cannot be derived') from exc
    return relative, target, _sha(expected)


def _evidence(task, state, saved, action_id, reviewer_id, evidence=None):
    relative, target, expected = _intent(task,saved,action_id)
    if action_id not in state['unknown_tool_calls']: raise LifecycleError('control record is not unknown for this action')
    if not target.is_file() or _sha(target.read_bytes()) != expected:
        raise LifecycleError('readback does not equal the exact pending write intent')
    for source in task['contract'].get('intake',{}).get('inputs',[]):
        path = Path(task['workspace']) / source['path']
        if _sha(path.read_bytes()) != source['sha256']: raise LifecycleError('STALE_INPUT')
    checks = [row for row in task['contract']['acceptance'] if row.get('assertion',{}).get('path') == relative]
    if not checks: raise LifecycleError('pending target has no bound deterministic acceptance check')
    base = {'kind':'readback','uri':relative,'sha256':expected,'claim':'Exact pending file write observed by host',
            'verdict':'PASS','check_id':checks[0]['id'],'environment':checks[0]['environment'],
            'tool_call_id':action_id,'observed_outcome':'SUCCEEDED',
            **{key:state[key] for key in ('task_id','revision','contract_hash','candidate')}}
    base['id'] = 'managed-readback-' + canonical_hash(base)[:32]
    ref = deepcopy(evidence) if evidence is not None else base
    if not isinstance(ref,dict) or any(ref.get(key) != base[key] for key in base if key not in {'id','claim'}):
        raise LifecycleError('readback evidence differs from bound pending intent')
    verify_evidence(ref,task['contract']['acceptance'],task['workspace'],reviewer_id=reviewer_id,owner=task['contract']['owner'])
    return ref


def make_readback_evidence(control_root, action_id, reviewer_id):
    task=load_task(control_root); database=_regular(task['state_db'])
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        state,saved,_=_records(db,task)
        if saved is None:raise LifecycleError('missing checkpoint')
        return _evidence(task,state,saved,action_id,reviewer_id)


def reconcile(control_root, action_id, evidence=None, reviewer_id=None, outcome='SUCCEEDED'):
    if outcome != 'SUCCEEDED': raise LifecycleError('NEEDS_ADAPTER: only exact successful file readback is supported')
    task=load_task(control_root); database=_regular(task['state_db'])
    with closing(sqlite3.connect(database,timeout=10)) as db:
        try:
            db.execute('BEGIN IMMEDIATE'); state,saved,lease=_records(db,task)
            if saved is None:raise LifecycleError('missing checkpoint')
            history=saved['snapshot'].get('reconciliations',[])
            prior=next((item for item in history if item.get('action_id')==action_id),None)
            if prior:
                retained=prior.get('evidence') if evidence is None else evidence
                if prior.get('evidence')!=retained or prior.get('reviewer_id')!=reviewer_id:
                    raise LifecycleError('RECONCILIATION_CONFLICT')
                verify_evidence(retained,task['contract']['acceptance'],task['workspace'],reviewer_id=reviewer_id,owner=task['contract']['owner'])
                db.rollback();return {'status':'RECONCILED','task_id':task['task_id'],'action_id':action_id,'reused':True}
            if not lease:raise LifecycleError('no current lease for acknowledged unknown action')
            if lease[2] <= time.time():raise LifecycleError('LEASE_EXPIRED: verify executor stop and reclaim separately')
            ref=_evidence(task,state,saved,action_id,reviewer_id,evidence)
            verifier=make_verifier(task['contract'],task['workspace'],reviewer_id=reviewer_id)
            stamp=datetime.now(timezone.utc).isoformat(); prefix='managed-'+canonical_hash({'action_id':action_id,'evidence':ref})
            def event(operation,payload,suffix):
                nonlocal state
                request={'protocol':state['protocol'],'task_id':state['task_id'],'revision':state['revision'],
                         'contract_hash':state['contract_hash'],'candidate':state['candidate'],
                         'request_id':prefix+'-'+suffix,'idempotency_key':prefix+'-'+suffix,'op':operation,'payload':payload}
                state,response=apply_event(state,request,verify_evidence=verifier)
                db.execute('INSERT INTO events(task_id,lease_id,operation,request_json,response_json,created_at) VALUES(?,?,?,?,?,?)',
                           (task['task_id'],lease[0],operation,json.dumps(request,ensure_ascii=False),json.dumps(response,ensure_ascii=False),stamp))
            event('evidence',ref,'evidence')
            event('reconcile',{'tool_call_id':action_id,'evidence_id':ref['id'],'observed_outcome':'SUCCEEDED'},'readback')
            token=prefix+'-resume'
            event('checkpoint',{'id':prefix,'resume_token':token,'cursor':{'next_step':saved['snapshot']['next_step']+1}},'checkpoint')
            event('pause',{'executor_stopped':True},'pause')
            # The observation and the committed release must still describe the
            # same bytes after all transition callbacks have executed.
            verify_evidence(ref,task['contract']['acceptance'],task['workspace'],reviewer_id=reviewer_id,owner=task['contract']['owner'])
            for source in task['contract'].get('intake',{}).get('inputs',[]):
                if _sha((Path(task['workspace'])/source['path']).read_bytes())!=source['sha256']:
                    raise LifecycleError('STALE_INPUT')
            snapshot=deepcopy(saved['snapshot']); observation={'status':'SUCCEEDED','evidence_id':ref['id'],'uri':ref['uri'],'sha256':ref['sha256']}
            snapshot['events'][-1]['result']=observation
            snapshot['pending']=None;snapshot['phase']='PAUSED';snapshot['last_status']='PAUSED'
            snapshot['next_step']=snapshot['events'][-1]['step']+1
            snapshot.setdefault('reconciliations',[]).append({'action_id':action_id,'observation':observation,'evidence':ref,'reviewer_id':reviewer_id})
            updated=db.execute('UPDATE agent_checkpoints SET generation=generation+1,snapshot=? WHERE task_id=? AND generation=?',
                               (json.dumps(snapshot,ensure_ascii=False,allow_nan=False),task['task_id'],saved['generation']))
            if updated.rowcount!=1:raise LifecycleError('CONCURRENT_CHECKPOINT_UPDATE')
            db.execute('UPDATE tasks SET state_json=?,status=?,updated_at=? WHERE task_id=?',
                       (json.dumps(state,ensure_ascii=False),state['status'],stamp,task['task_id']))
            db.execute('DELETE FROM leases WHERE task_id=? AND lease_id=?',(task['task_id'],lease[0]))
            db.commit()
            return {'status':'RECONCILED','task_id':task['task_id'],'action_id':action_id,'reused':False}
        except Exception:
            db.rollback();raise
