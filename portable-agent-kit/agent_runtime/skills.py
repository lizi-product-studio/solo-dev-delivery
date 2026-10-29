"""Persistent tool recipes; activation is gated by bound regression evidence.

Reviewer identity is a host attestation, not authenticated by a string. The
library verifies receipt bindings and file hashes; it does not decide whether
a regression's business assertions are sufficient.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from .recipes import compile_traces, execute_recipe,SkillPreflightError
from .tools import ToolError


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class SkillLibrary:
    def __init__(self, database):
        self.database=Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS skills(name TEXT PRIMARY KEY, description TEXT NOT NULL, spec TEXT NOT NULL, status TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 0)")
            columns={row[1] for row in db.execute('PRAGMA table_info(skills)')}
            for name, declaration in (('version','INTEGER NOT NULL DEFAULT 1'),
                                      ('spec_sha256','TEXT'), ('owner',"TEXT NOT NULL DEFAULT 'agent-runtime'")):
                if name not in columns:
                    db.execute('ALTER TABLE skills ADD COLUMN '+name+' '+declaration)
            # Older ACTIVE rows have never passed the new evidence gate.
            # Keep their contents, but require regression before use again.
            for name, raw in db.execute('SELECT name,spec FROM skills WHERE spec_sha256 IS NULL').fetchall():
                db.execute("UPDATE skills SET spec_sha256=?,status='CANDIDATE' WHERE name=?",
                           (spec_hash(json.loads(raw)),name))
            db.execute("CREATE TABLE IF NOT EXISTS skill_experiences(task_id TEXT PRIMARY KEY, goal TEXT NOT NULL, events TEXT NOT NULL, success INTEGER NOT NULL)")
            db.execute('CREATE TABLE IF NOT EXISTS skill_canary_activations(name TEXT NOT NULL,version INTEGER NOT NULL,spec_sha256 TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(name,version,spec_sha256))')
            db.execute("CREATE TABLE IF NOT EXISTS skill_activations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL, spec_sha256 TEXT NOT NULL, receipt_path TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, reviewer_id TEXT NOT NULL)")
            db.execute('CREATE TABLE IF NOT EXISTS skill_status_events(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS skill_versions(name TEXT NOT NULL,version INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(name,version))')
            db.execute('''CREATE TABLE IF NOT EXISTS skill_failures(
                name TEXT NOT NULL,version INTEGER NOT NULL,task_id TEXT NOT NULL,
                spec_sha256 TEXT NOT NULL,outcome TEXT NOT NULL,action_id TEXT NOT NULL,
                receipt_path TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,quarantined INTEGER NOT NULL,
                PRIMARY KEY(name,version,task_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS skill_health_observations(
                name TEXT NOT NULL,version INTEGER NOT NULL,task_id TEXT NOT NULL,
                spec_sha256 TEXT NOT NULL,outcome TEXT NOT NULL,receipt_path TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,decision TEXT NOT NULL,
                PRIMARY KEY(name,version,task_id))''')

    def register(self, name, description, spec, *, status='CANDIDATE', owner='agent-runtime',if_absent=False):
        if status!='CANDIDATE':
            raise ValueError('register only accepts CANDIDATE; use activate with evidence')
        if not isinstance(name,str) or not name.strip() or not isinstance(owner,str) or not owner.strip():
            raise ValueError('skill name and owner must be non-empty strings')
        if not isinstance(spec,dict):
            raise ValueError('skill spec must be an object')
        digest=spec_hash(spec)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT version,spec_sha256,owner FROM skills WHERE name=?',(name,)).fetchone()
            if row and if_absent:return self._get(db,name)
            if row and row[1]==digest and row[2]==owner:
                return self._get(db,name)
            if row:
                self._archive(db,self._get(db,name))
            maximum=db.execute('SELECT MAX(version) FROM skill_versions WHERE name=?',(name,)).fetchone()[0] or 0
            version=max(row[0] if row else 0,maximum)+1
            db.execute("INSERT INTO skills(name,description,spec,status,uses,version,spec_sha256,owner) VALUES(?,?,?,'CANDIDATE',0,?,?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description,spec=excluded.spec,status='CANDIDATE',version=excluded.version,spec_sha256=excluded.spec_sha256,owner=excluded.owner",
                       (name,str(description),json.dumps(spec,ensure_ascii=False),version,digest,owner))
            return self._get(db,name)

    @staticmethod
    def _get(db,name):
        row=db.execute('SELECT name,description,spec,status,uses,version,spec_sha256,owner FROM skills WHERE name=?',(name,)).fetchone()
        if row is None:
            raise ValueError('unknown skill')
        return dict(zip(('name','description','spec','status','uses','version','spec_sha256','owner'),
                        (row[0],row[1],json.loads(row[2]),*row[3:])))

    def get(self,name):
        with self._db() as db:
            return self._get(db,name)

    @staticmethod
    def _archive(db,row):
        db.execute('INSERT OR REPLACE INTO skill_versions(name,version,snapshot) VALUES(?,?,?)',
                   (row['name'],row['version'],json.dumps(row,ensure_ascii=False)))

    def promote(self,name):
        raise ValueError('ungated promote is disabled; use activate(name, receipt_path)')

    @staticmethod
    def _verify_evidence(refs,root):
        if not isinstance(refs,list) or not refs:
            raise ValueError('regression check requires hashed evidence_refs')
        for ref in refs:
            if not isinstance(ref,dict) or not isinstance(ref.get('uri'),str):
                raise ValueError('invalid evidence reference')
            uri=Path(ref['uri'])
            if uri.is_absolute() or '..' in uri.parts:
                raise ValueError('evidence must remain relative to receipt directory')
            path=root/uri
            if any(p.is_symlink() for p in (path,*path.parents)):
                raise ValueError('symlink evidence is not allowed')
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                raise ValueError('evidence escapes receipt directory') from None
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):
                raise ValueError('regression evidence hash mismatch')

    def activate(self,name,regression_receipt,*,canary_receipt=None):
        if not isinstance(regression_receipt,(str,Path)):
            raise ValueError('activation requires a saved regression receipt file')
        path=Path(regression_receipt)
        if path.is_symlink() or not path.is_file():
            raise ValueError('regression receipt file is missing or linked')
        raw=path.read_bytes()
        receipt=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(receipt,dict) or receipt.get('format')!='skill-regression/1' or receipt.get('status')!='PASS':
            raise ValueError('activation requires a skill-regression/1 PASS receipt')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            skill=self._get(db,name)
            if skill['spec'].get('failure_fingerprint'):
                if canary_receipt is None:raise ValueError('failure-derived improvement requires an observed gain canary')
                from .improvement_canary import verify_canary
                verified_canary=verify_canary(canary_receipt,skill,path)
                baseline=self._get(db,verified_canary['baseline']['name'])
                if any(baseline[key]!=verified_canary['baseline'][key] for key in ('version','spec_sha256')):
                    raise ValueError('canary baseline changed before activation')
            if skill['status']!='CANDIDATE' and not (skill['status']=='ACTIVE' and skill['spec'].get('failure_fingerprint') and canary_receipt is not None):
                raise ValueError('only candidate skills can be activated')
            for field,expected in (('skill',name),('version',skill['version']),('spec_sha256',skill['spec_sha256']),('owner',skill['owner'])):
                if receipt.get(field)!=expected:
                    raise ValueError('regression receipt binding mismatch: '+field)
            reviewer=receipt.get('reviewer_id')
            if not isinstance(reviewer,str) or not reviewer.strip() or reviewer==skill['owner']:
                raise ValueError('independent reviewer must differ from owner')
            checks=receipt.get('checks')
            if not isinstance(checks,list) or not checks:
                raise ValueError('regression receipt requires checks')
            seen=set()
            for check in checks:
                if not isinstance(check,dict) or not isinstance(check.get('id'),str) or not check['id'].strip() or check['id'] in seen or check.get('status')!='PASS':
                    raise ValueError('regression checks must be unique and PASS')
                seen.add(check['id'])
                self._verify_evidence(check.get('evidence_refs'),path.parent)
            db.execute("UPDATE skills SET status='ACTIVE' WHERE name=?",(name,))
            if canary_receipt is not None and skill['spec'].get('failure_fingerprint'):
                canary_path=Path(canary_receipt).resolve()
                db.execute('INSERT OR REPLACE INTO skill_canary_activations VALUES(?,?,?,?,?)',
                    (name,skill['version'],skill['spec_sha256'],str(canary_path),hashlib.sha256(canary_path.read_bytes()).hexdigest()))
            db.execute('INSERT INTO skill_activations(name,version,spec_sha256,receipt_path,receipt_sha256,reviewer_id) VALUES(?,?,?,?,?,?)',
                       (name,skill['version'],skill['spec_sha256'],str(path.resolve()),hashlib.sha256(raw).hexdigest(),reviewer))
            self._archive(db,self._get(db,name))
            return self._get(db,name)

    def list_active(self):
        with self._db() as db:
            return [self._get(db,row[0]) for row in db.execute("SELECT name FROM skills WHERE status='ACTIVE' ORDER BY uses DESC").fetchall()]

    def list_all(self):
        with self._db() as db:
            return [self._get(db,row[0]) for row in db.execute('SELECT name FROM skills ORDER BY name').fetchall()]

    def record_failure(self,name,run_receipt,receipt_sha256,*,quarantine=False):
        """Archive a trusted host receipt; prose from a model is not evidence.

        Runtime must bind the invoked skill version in the event and record the
        same failed/unknown action in control-plane events. The caller supplies
        the previously observed file hash. This does not authenticate host identity.
        """
        if type(quarantine) is not bool:raise ValueError('quarantine must be boolean')
        path=Path(run_receipt)
        if path.is_symlink() or not path.is_file():raise ValueError('saved host receipt is required')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt_sha256:raise ValueError('failure receipt hash mismatch')
        report=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(report,dict) or report.get('status') not in {'FAILED','CONTROL_BLOCKED','NEEDS_RECONCILIATION','BUDGET_EXHAUSTED'}:
            raise ValueError('receipt is not a failed or unknown runtime run')
        task_id=report.get('task_id');state=report.get('control_state',{})
        if not isinstance(task_id,str) or not task_id or not isinstance(state,dict) or state.get('task_id')!=task_id:
            raise ValueError('failure receipt needs a matching control task')
        if (type(state.get('revision')) is not int or not isinstance(state.get('contract_hash'),str)
                or not isinstance(state.get('candidate'),str)):
            raise ValueError('failure receipt needs current control version bindings')
        failures=[]
        for event in report.get('events',[]):
            if not isinstance(event,dict) or event.get('action')!='skill' or event.get('decision',{}).get('name')!=name:continue
            outcome=event.get('result',{}).get('status') if isinstance(event.get('result'),dict) else None
            binding=event.get('skill_binding',{})
            if (outcome not in {'FAILED','UNKNOWN'} or binding.get('name')!=name or type(binding.get('version')) is not int
                    or type(event.get('step')) is not int or event['step']<1):continue
            action_id=task_id+'-step-'+str(event.get('step'))
            if action_id not in state.get('unknown_tool_calls' if outcome=='UNKNOWN' else 'failed_tool_calls',[]):continue
            matched=any(isinstance(row,dict) and row.get('operation')=='tool_result'
                        and row.get('request',{}).get('task_id')==task_id
                        and all(row.get('request',{}).get(key)==state[key] for key in ('revision','contract_hash','candidate'))
                        and row.get('request',{}).get('payload',{}).get('tool_call_id')==action_id
                        and row.get('request',{}).get('payload',{}).get('status')==outcome
                        for row in report.get('control_events',[]))
            if matched:failures.append((binding,outcome,action_id))
        if not failures:raise ValueError('no version-bound skill failure with matching host control event')
        binding,outcome,action_id=failures[-1];version=binding['version'];digest=binding.get('spec_sha256')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._get(db,name)
            if current['version']==version:source=current
            else:
                archived=db.execute('SELECT snapshot FROM skill_versions WHERE name=? AND version=?',(name,version)).fetchone()
                if not archived:raise ValueError('failure skill version is unknown')
                source=json.loads(archived[0])
            if source['spec_sha256']!=digest:raise ValueError('failure skill spec binding mismatch')
            prior=db.execute('SELECT receipt_sha256,quarantined FROM skill_failures WHERE name=? AND version=? AND task_id=?',
                             (name,version,task_id)).fetchone()
            if prior:
                if prior[0]!=receipt_sha256:raise ValueError('failure already recorded with different evidence')
                return {'name':name,'version':version,'task_id':task_id,'outcome':outcome,'quarantined':bool(prior[1]),'reused':True}
            disabled=quarantine and current['version']==version and current['spec_sha256']==digest and current['status']=='ACTIVE'
            if disabled:
                db.execute("UPDATE skills SET status='DISABLED' WHERE name=? AND version=?",(name,version))
                db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                           (name,version,'DISABLED','host-observed skill failure in task '+task_id))
            db.execute('INSERT INTO skill_failures VALUES(?,?,?,?,?,?,?,?,?)',
                       (name,version,task_id,digest,outcome,action_id,str(path.resolve()),receipt_sha256,int(disabled)))
            db.execute('INSERT OR IGNORE INTO skill_experiences VALUES(?,?,?,0)',
                       (task_id,str(state.get('contract',{}).get('goal','failed skill invocation')),json.dumps(report['events'],ensure_ascii=False)))
            return {'name':name,'version':version,'task_id':task_id,'outcome':outcome,'quarantined':disabled,'reused':False}

    def failure_history(self,name=None):
        with self._db() as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT * FROM skill_failures'+(' WHERE name=?' if name is not None else '')+' ORDER BY task_id',
                            (name,) if name is not None else ()).fetchall()
            return [dict(row) for row in rows]

    def monitor_run(self,receipt):
        """Quarantine only an attributable, independently observed final failure.

        Restore only an archived activation with an earlier observed task PASS,
        no observed failures, and unchanged regression/observation evidence.
        No task action is retried by this operation.
        """
        from .skill_monitor import observe_health
        observed=observe_health(receipt)
        name=observed['name'];version=observed['version']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._get(db,name)
            if current['version']==version:source=current
            else:
                saved=db.execute('SELECT snapshot FROM skill_versions WHERE name=? AND version=?',(name,version)).fetchone()
                if saved is None:raise ValueError('observed skill version is unknown')
                source=json.loads(saved[0])
            if source['spec_sha256']!=observed['spec_sha256']:raise ValueError('health observation has another skill specification')
            prior=db.execute('SELECT receipt_sha256,decision FROM skill_health_observations WHERE name=? AND version=? AND task_id=?',
                (name,version,observed['task_id'])).fetchone()
            if prior:
                if prior[0]!=observed['receipt_sha256']:raise ValueError('health observation already has different evidence')
                return {**json.loads(prior[1]),'reused':True}
            result={**observed,'status':'RECORDED','quarantined':False,'rolled_back_to':None,'reused':False}
            managed=bool(source['spec'].get('failure_fingerprint')) or source['spec'].get('health_policy')=='quarantine-on-independent-candidate-failure'
            result['managed_policy']=managed
            if observed['outcome']=='FAIL' and managed and current['version']==version and current['status']=='ACTIVE':
                db.execute("UPDATE skills SET status='DISABLED' WHERE name=? AND version=?",(name,version))
                db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                    (name,version,'DISABLED','independent candidate failure in task '+observed['task_id']))
                result['quarantined']=True
                candidates=db.execute('''SELECT h.version,h.receipt_path,h.receipt_sha256,v.snapshot
                    FROM skill_health_observations h JOIN skill_versions v ON h.name=v.name AND h.version=v.version
                    WHERE h.name=? AND h.version<>? AND h.outcome='PASS'
                    AND NOT EXISTS(SELECT 1 FROM skill_health_observations f WHERE f.name=h.name AND f.version=h.version AND f.outcome='FAIL')
                    ORDER BY h.version DESC''',(name,version)).fetchall()
                for old_version,path,digest,snapshot in candidates:
                    target=json.loads(snapshot)
                    if target['status']!='ACTIVE':continue
                    if not (target['spec'].get('failure_fingerprint') or target['spec'].get('health_policy')=='quarantine-on-independent-candidate-failure'):continue
                    try:
                        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:continue
                        previous=observe_health(path)
                        if previous['outcome']!='PASS' or previous['version']!=old_version or previous['spec_sha256']!=target['spec_sha256']:continue
                        self._check_activation(target)
                    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):continue
                    self._archive(db,self._get(db,name))
                    db.execute("UPDATE skills SET description=?,spec=?,status='ACTIVE',uses=?,version=?,spec_sha256=?,owner=? WHERE name=?",
                        (target['description'],json.dumps(target['spec'],ensure_ascii=False),target['uses'],
                         target['version'],target['spec_sha256'],target['owner'],name))
                    db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                        (name,old_version,'ROLLED_BACK','automatic restore after independently observed v%d failure'%version))
                    result['rolled_back_to']=old_version
                    break
            db.execute('INSERT INTO skill_health_observations VALUES(?,?,?,?,?,?,?,?)',
                (name,version,observed['task_id'],observed['spec_sha256'],observed['outcome'],
                 observed['receipt_path'],observed['receipt_sha256'],json.dumps(result,ensure_ascii=False)))
            return result

    def health_history(self,name=None):
        with self._db() as db:
            rows=db.execute('SELECT decision FROM skill_health_observations'+(' WHERE name=?' if name else '')+' ORDER BY rowid',
                (name,) if name else ()).fetchall()
            return [json.loads(row[0]) for row in rows]

    def record_experience(self,task_id,goal,events,*,success):
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO skill_experiences(task_id,goal,events,success) VALUES(?,?,?,?)',
                       (task_id,str(goal),json.dumps(events,ensure_ascii=False),int(bool(success))))

    def execute(self,name,arguments,tools,*,expected_version=None,expected_spec_sha256=None):
        # Only this pure lookup/evidence phase may be translated wholesale.
        # Execution and use-count bookkeeping below may follow real effects.
        try:
            row=self.get(name)
            if ((expected_version is not None and (type(expected_version) is not int or row['version']!=expected_version))
                    or (expected_spec_sha256 is not None and row['spec_sha256']!=expected_spec_sha256)):
                raise ValueError('skill invocation version/spec changed before execution')
            if row['status']!='ACTIVE':raise ValueError('skill is not ACTIVE')
            if not isinstance(arguments,dict):raise ValueError('skill arguments must be an object')
            if not isinstance(row['spec'],dict):raise ValueError('skill spec must be an object')
            self._check_activation(row)
        except (ValueError,OSError,sqlite3.Error,TypeError,KeyError) as exc:
            raise SkillPreflightError(str(exc)) from exc
        spec=row['spec']
        if spec.get('format') == 'tool-recipe/1':
            result=execute_recipe(spec,arguments,tools)
        else:
            if not isinstance(spec.get('tool'),str):
                raise SkillPreflightError('skill has no executable tool recipe; review candidate first')
            try:
                merged=dict(spec.get('arguments',{}));merged.update(arguments)
                tools.validate(spec['tool'],merged)
            except (ToolError,ValueError,TypeError) as exc:raise SkillPreflightError(str(exc)) from exc
            try:result=tools.call(spec['tool'],merged)
            except ToolError as exc:raise SkillPreflightError(str(exc)) from exc
        with self._db() as db:
            db.execute('UPDATE skills SET uses=uses+1 WHERE name=? AND version=?',(name,row['version']))
        return result

    def _check_activation(self,row):
        if spec_hash(row['spec'])!=row['spec_sha256']:
            raise ValueError('skill spec was modified outside version control')
        with self._db() as db:
            canary=db.execute('SELECT path,sha256 FROM skill_canary_activations WHERE name=? AND version=? AND spec_sha256=?',
                              (row['name'],row['version'],row['spec_sha256'])).fetchone()
            activation=db.execute('SELECT receipt_path,receipt_sha256 FROM skill_activations WHERE name=? AND version=? AND spec_sha256=? ORDER BY id DESC LIMIT 1',
                                  (row['name'],row['version'],row['spec_sha256'])).fetchone()
        if activation is None:
            raise ValueError('activation evidence missing')
        path=Path(activation[0])
        if row['spec'].get('failure_fingerprint'):
            if canary is None or hashlib.sha256(Path(canary[0]).read_bytes()).hexdigest()!=canary[1]:raise ValueError('improvement canary evidence missing or stale')
            from .improvement_canary import verify_canary
            verify_canary(canary[0],row,path)
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=activation[1]:
            raise ValueError('activation receipt became stale')
        value=json.loads(raw)
        for check in value['checks']:
            self._verify_evidence(check['evidence_refs'],path.parent)

    def disable(self,name,*,reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError('disable requires reason')
        with self._db() as db:
            row=self._get(db,name)
            db.execute("UPDATE skills SET status='DISABLED' WHERE name=?",(name,))
            db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                       (name,row['version'],'DISABLED',reason))

    def restore(self,name,*,reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError('restore requires reason')
        row=self.get(name)
        if row['status']!='DISABLED':
            raise ValueError('only disabled skill can be restored')
        self._check_activation(row)
        with self._db() as db:
            db.execute("UPDATE skills SET status='ACTIVE' WHERE name=? AND version=?",(name,row['version']))
            db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                       (name,row['version'],'ACTIVE',reason))

    def rollback(self,name,version,*,reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError('rollback requires reason')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._get(db,name)
            saved=db.execute('SELECT snapshot FROM skill_versions WHERE name=? AND version=?',(name,version)).fetchone()
            if saved is None or version==current['version']:
                raise ValueError('a different archived version is required')
            target=json.loads(saved[0])
            if target['status']!='ACTIVE':
                raise ValueError('rollback target was never activated')
            self._check_activation(target)
            self._archive(db,current)
            db.execute("UPDATE skills SET description=?,spec=?,status='ACTIVE',uses=?,version=?,spec_sha256=?,owner=? WHERE name=?",
                       (target['description'],json.dumps(target['spec'],ensure_ascii=False),target['uses'],
                        target['version'],target['spec_sha256'],target['owner'],name))
            db.execute('INSERT INTO skill_status_events(name,version,status,reason) VALUES(?,?,?,?)',
                       (name,version,'ROLLED_BACK','from v%d: '%current['version']+reason))
            return self._get(db,name)

    def propose_from_repeated_success(self,goal,*,min_tasks=2):
        if isinstance(min_tasks,bool) or not isinstance(min_tasks,int) or min_tasks<2:
            raise ValueError('at least two distinct tasks are required')
        with self._db() as db:
            rows=db.execute("SELECT task_id,events FROM skill_experiences WHERE success=1 AND goal=? ORDER BY task_id",(str(goal),)).fetchall()
        if len({row[0] for row in rows})<min_tasks:
            return None
        name='candidate-'+'-'.join(str(goal).lower().split())[:48]
        spec=compile_traces([json.loads(row[1]) for row in rows])
        if spec is None:
            return None
        spec.update(trigger=str(goal),observed_tasks=[row[0] for row in rows])
        registered=self.register(name,'learned from repeated successful tasks',spec)
        return {'name':name,'status':'CANDIDATE','tasks':len(rows),
                'version':registered['version'],'spec_sha256':registered['spec_sha256']}

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.database)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
