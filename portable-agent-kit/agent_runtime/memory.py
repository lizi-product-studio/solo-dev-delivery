"""Project-scoped persistent memory with provenance and correction history.

Search is lexical (English tokens and Chinese character bigrams), not semantic
or embedding retrieval. Archived facts remain available for audit, while normal
retrieval excludes expired, withdrawn and superseded records.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import time


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be a non-empty string')
    return value.strip()


def _sources(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('source_refs must be a list')
    for ref in value:
        if isinstance(ref, str):
            _text(ref, 'source reference')
        elif isinstance(ref, dict):
            _text(ref.get('uri'), 'source reference uri')
        else:
            raise ValueError('source_refs items must be strings or objects with uri')
    # Round-trip copies caller data and rejects unserializable references.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _expiry(value):
    if value is None:
        return None
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('expires_at ISO date must contain a timezone')
        value = parsed.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('expires_at must be a finite Unix timestamp or timezone-aware ISO date')
    return float(value)


def _terms(query):
    query = str(query).casefold()
    result = set(re.findall(r'[a-z0-9_]+', query))
    for run in re.findall(r'[\u3400-\u9fff]+', query):
        if len(run) == 1:
            result.add(run)
        else:
            result.update(run[index:index+2] for index in range(len(run)-1))
    return result


class MemoryStore:
    def __init__(self, database, *, scope='local', project_id=None):
        if project_id is not None:
            if scope != 'local' and scope != project_id:
                raise ValueError('scope and project_id must refer to the same project')
            scope = project_id
        self.scope = _text(scope, 'scope')
        self.project_id = self.scope
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                task_id TEXT, text TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(memories)')}
            for name, declaration in (
                ('project_id', "TEXT NOT NULL DEFAULT 'local'"),
                ('source_refs', "TEXT NOT NULL DEFAULT '[]'"),
                ('expires_at', 'REAL'), ('retracted_at', 'REAL'),
                ('retraction_reason', 'TEXT'), ('superseded_by', 'INTEGER'),
                ('corrects_id', 'INTEGER'),
            ):
                if name not in columns:
                    db.execute('ALTER TABLE memories ADD COLUMN ' + name + ' ' + declaration)
            db.execute('CREATE INDEX IF NOT EXISTS memories_scope_idx ON memories(project_id, created)')
            db.execute('''CREATE TABLE IF NOT EXISTS memory_changes(
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                memory_id INTEGER NOT NULL, operation TEXT NOT NULL,
                detail TEXT NOT NULL, created REAL NOT NULL)''')

    def _insert(self, db, text, *, kind, task_id, data, source_refs, expires_at, corrects_id=None):
        cursor = db.execute('''INSERT INTO memories(
            kind,task_id,text,data,created,project_id,source_refs,expires_at,corrects_id)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (kind, task_id, str(text), json.dumps(data or {}, ensure_ascii=False, allow_nan=False),
             time.time(), self.scope, json.dumps(source_refs, ensure_ascii=False), expires_at, corrects_id))
        return cursor.lastrowid

    def add(self, text, *, kind='episode', task_id=None, data=None, source_refs=None, expires_at=None):
        refs, expiry = _sources(source_refs), _expiry(expires_at)
        with self._db() as db:
            return self._insert(db, text, kind=kind, task_id=task_id, data=data,
                                source_refs=refs, expires_at=expiry)

    def _get(self, db, memory_id):
        row = db.execute('SELECT * FROM memories WHERE id=? AND project_id=?',
                         (memory_id, self.scope)).fetchone()
        if row is None:
            raise ValueError('memory not found in current project')
        result = dict(row)
        result['data'] = json.loads(result['data'])
        result['source_refs'] = json.loads(result['source_refs'])
        result['scope'] = result['project_id']
        return result

    def get(self, memory_id):
        """Read one scoped record for audit, including archived facts."""
        with self._db() as db:
            return self._get(db, memory_id)

    def retract(self, memory_id, reason):
        reason = _text(reason, 'reason')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._get(db, memory_id)
            now = time.time()
            db.execute('UPDATE memories SET retracted_at=?,retraction_reason=? WHERE id=? AND project_id=?',
                       (now, reason, memory_id, self.scope))
            db.execute('INSERT INTO memory_changes(project_id,memory_id,operation,detail,created) VALUES(?,?,?,?,?)',
                       (self.scope, memory_id, 'retract', json.dumps({'reason': reason}, ensure_ascii=False), now))
            return self._get(db, memory_id)

    def correct(self, memory_id, newtext, source_refs):
        newtext = _text(newtext, 'newtext')
        refs = _sources(source_refs)
        if not refs:
            raise ValueError('correction requires source_refs')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._get(db, memory_id)
            if old['retracted_at'] is not None or old['superseded_by'] is not None:
                raise ValueError('only a current unretracted memory can be corrected')
            new_id = self._insert(db, newtext, kind=old['kind'], task_id=old['task_id'], data=old['data'],
                                  source_refs=refs, expires_at=old['expires_at'], corrects_id=memory_id)
            db.execute('UPDATE memories SET superseded_by=? WHERE id=? AND project_id=?',
                       (new_id, memory_id, self.scope))
            db.execute('INSERT INTO memory_changes(project_id,memory_id,operation,detail,created) VALUES(?,?,?,?,?)',
                       (self.scope, memory_id, 'correct', json.dumps({'replacement_id': new_id}), time.time()))
            return new_id

    def history(self, memory_id):
        with self._db() as db:
            self._get(db, memory_id)
            rows = db.execute('SELECT * FROM memory_changes WHERE project_id=? AND memory_id=? ORDER BY id',
                              (self.scope, memory_id)).fetchall()
            return [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows]

    def search(self, query, limit=8):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError('limit must be an integer from 1 to 1000')
        terms = _terms(query)
        if not terms:
            return []
        with self._db() as db:
            rows = db.execute('''SELECT id,text,created FROM memories WHERE project_id=?
                AND retracted_at IS NULL AND superseded_by IS NULL
                AND (expires_at IS NULL OR expires_at>?)''', (self.scope, time.time())).fetchall()
            scored = []
            for row in rows:
                lowered = row['text'].casefold()
                score = sum(term in lowered for term in terms)
                if score:
                    scored.append((score, row['created'], row['id']))
            chosen = sorted(scored, key=lambda row: (row[0], row[1], row[2]), reverse=True)[:limit]
            return [self._get(db, memory_id) for _, _, memory_id in chosen]

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
