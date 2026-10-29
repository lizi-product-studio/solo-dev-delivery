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
from .recipes import compile_traces, execute_recipe


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
            db.execute("CREATE TABLE IF NOT EXISTS skill_activations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL, spec_sha256 TEXT NOT NULL, receipt_path TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, reviewer_id TEXT NOT NULL)")
            db.execute('CREATE TABLE IF NOT EXISTS skill_status_events(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS skill_versions(name TEXT NOT NULL,version INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(name,version))')

    def register(self, name, description, spec, *, status='CANDIDATE', owner='agent-runtime'):
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

    def activate(self,name,regression_receipt):
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
            if skill['status']!='CANDIDATE':
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
            db.execute('INSERT INTO skill_activations(name,version,spec_sha256,receipt_path,receipt_sha256,reviewer_id) VALUES(?,?,?,?,?,?)',
                       (name,skill['version'],skill['spec_sha256'],str(path.resolve()),hashlib.sha256(raw).hexdigest(),reviewer))
            self._archive(db,self._get(db,name))
            return self._get(db,name)

    def list_active(self):
        with self._db() as db:
            return [self._get(db,row[0]) for row in db.execute("SELECT name FROM skills WHERE status='ACTIVE' ORDER BY uses DESC").fetchall()]

    def record_experience(self,task_id,goal,events,*,success):
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO skill_experiences(task_id,goal,events,success) VALUES(?,?,?,?)',
                       (task_id,str(goal),json.dumps(events,ensure_ascii=False),int(bool(success))))

    def execute(self,name,arguments,tools):
        row=self.get(name)
        if row['status']!='ACTIVE':
            raise ValueError('skill is not ACTIVE')
        self._check_activation(row)
        spec=row['spec']
        if spec.get('format') == 'tool-recipe/1':
            result=execute_recipe(spec,arguments,tools)
        else:
            if not isinstance(spec.get('tool'),str):
                raise ValueError('skill has no executable tool recipe; review candidate first')
            merged=dict(spec.get('arguments',{}));merged.update(arguments)
            result=tools.call(spec['tool'],merged)
        with self._db() as db:
            db.execute('UPDATE skills SET uses=uses+1 WHERE name=? AND version=?',(name,row['version']))
        return result

    def _check_activation(self,row):
        if spec_hash(row['spec'])!=row['spec_sha256']:
            raise ValueError('skill spec was modified outside version control')
        with self._db() as db:
            activation=db.execute('SELECT receipt_path,receipt_sha256 FROM skill_activations WHERE name=? AND version=? AND spec_sha256=? ORDER BY id DESC LIMIT 1',
                                  (row['name'],row['version'],row['spec_sha256'])).fetchone()
        if activation is None:
            raise ValueError('activation evidence missing')
        path=Path(activation[0])
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
