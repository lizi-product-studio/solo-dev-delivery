"""Publish only the current invocation's stopped checkpoint result.

The SQLite write lock serializes publication with checkpoint advancement. Each
invocation also has an immutable receipt; shared files are a guarded view, never
the source from which another invocation obtains its own result.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


class PublicationError(ValueError):
    pass


def _bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')


def _plain(path):
    path=Path(path).absolute()
    if any(item.is_symlink() or getattr(item,'is_junction',lambda:False)() for item in (path,*path.parents)):
        raise PublicationError('publication path must not contain links')
    if path.exists() and not path.is_file():raise PublicationError('publication target is not a file')
    return path


def _atomic(path,data):
    path=_plain(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:stream.write(data)
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def immutable_receipt_path(receipt_path,invocation_id):
    if (not isinstance(invocation_id,str) or len(invocation_id)!=32
            or any(c not in '0123456789abcdef' for c in invocation_id)):
        raise PublicationError('invocation id must be a host-generated UUID hex value')
    return Path(receipt_path).absolute().parent/'runs'/invocation_id/'receipt.json'


def write_immutable_report(receipt_path,report):
    destination=_plain(immutable_receipt_path(receipt_path,report['invocation_id']))
    payload=_bytes(report)
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with destination.open('xb') as stream:stream.write(payload)
    except FileExistsError:
        if destination.read_bytes()!=payload:raise PublicationError('immutable invocation receipt differs')
    return destination


def publish_report(receipt_path,report,*,state_db,control_db=None,intake=None,lease_id=None):
    """CAS-publish runtime and intake views for the same owned generation.

    A stale invocation leaves both shared files untouched. Holding the database
    lock across replacement prevents a new worker from advancing while these
    files are published. Separate control state is attached before the immediate
    transaction, so journal generation and control ownership are locked and
    observed by the same connection. OS failures may leave a partial view; the SQLite task
    and immutable receipt remain authoritative and a same-generation retry can
    rematerialize the view. This does not claim a filesystem-wide transaction.
    """
    receipt_path=_plain(receipt_path);database=_plain(state_db)
    generation=report.get('checkpoint_generation');identity=report.get('invocation_id')
    immutable=immutable_receipt_path(receipt_path,identity)
    report_bytes=_bytes(report);intake_bytes=_bytes(intake) if intake is not None else None
    if type(generation) is not int:return {'status':'NOT_OWNED','published':False}
    if not database.is_file():raise PublicationError('checkpoint database does not exist')
    needs_control=lease_id is not None or 'control_state' in report
    control_database=_plain(control_db if control_db is not None else state_db) if needs_control else None
    if control_database is not None and not control_database.is_file():
        raise PublicationError('control database does not exist')
    with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=10)) as db:
        try:
            control_schema='main'
            if control_database is not None and not os.path.samefile(database,control_database):
                db.execute('ATTACH DATABASE ? AS managed_control',(control_database.as_uri()+'?mode=rw',))
                control_schema='managed_control'
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT generation,snapshot FROM agent_checkpoints WHERE task_id=?',(report['task_id'],)).fetchone()
            if row is None:return {'status':'NOT_OWNED','published':False}
            snapshot=json.loads(row[1])
            if (row[0]!=generation or snapshot.get('invocation_id')!=identity
                    or snapshot.get('last_status')!=report['status']):
                return {'status':'STALE_PUBLICATION','published':False}
            if needs_control:
                tables={row[0] for row in db.execute('SELECT name FROM '+control_schema+".sqlite_master WHERE type='table'")}
                if not {'tasks','leases'}<=tables:return {'status':'MISSING_CONTROL_STATE','published':False}
            if lease_id is not None:
                lease=db.execute('SELECT lease_id FROM '+control_schema+'.leases WHERE task_id=?',(report['task_id'],)).fetchone()
                if lease is None or lease[0]!=lease_id:return {'status':'STALE_OWNERSHIP','published':False}
            if 'control_state' in report:
                control=db.execute('SELECT state_json FROM '+control_schema+'.tasks WHERE task_id=?',(report['task_id'],)).fetchone()
                if control is None or json.loads(control[0])!=report['control_state']:
                    return {'status':'STALE_CONTROL_STATE','published':False}
            db.execute('''CREATE TABLE IF NOT EXISTS agent_result_publications(
                task_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,invocation_id TEXT NOT NULL,
                report_sha256 TEXT NOT NULL,intake_sha256 TEXT,immutable_receipt TEXT NOT NULL,published_at TEXT NOT NULL)''')
            digest=hashlib.sha256(report_bytes).hexdigest()
            intake_digest=hashlib.sha256(intake_bytes).hexdigest() if intake_bytes is not None else None
            prior=db.execute('SELECT generation,invocation_id,report_sha256,intake_sha256 FROM agent_result_publications WHERE task_id=?',
                             (report['task_id'],)).fetchone()
            if prior and prior[0]==generation and tuple(prior[1:])!=(identity,digest,intake_digest):
                raise PublicationError('PUBLICATION_CONFLICT for the same checkpoint generation')
            _atomic(receipt_path,report_bytes)
            if intake_bytes is not None:_atomic(receipt_path.parent/'intake-receipt.json',intake_bytes)
            db.execute('INSERT OR REPLACE INTO agent_result_publications VALUES(?,?,?,?,?,?,?)',
                       (report['task_id'],generation,identity,digest,intake_digest,str(immutable),datetime.now(timezone.utc).isoformat()))
            db.commit()
            return {'status':'PUBLISHED','published':True,'invocation_receipt':str(immutable)}
        except Exception:
            db.rollback();raise
