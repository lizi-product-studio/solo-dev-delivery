"""Persistent bounded task queue for synchronous host workers.

The queue stores JSON envelopes; it never executes payload code, starts a
daemon, retries an unknown effect or steals a stale worker's task. Runtime
completion/acceptance belongs to the trusted worker; this layer only enforces
ownership, dependency ordering and durable result classification.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3


class SchedulerError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise SchedulerError(name + ' must be a non-empty string')
    return value.strip()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat()


class TaskScheduler:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS scheduled_tasks(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    claim_token TEXT,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scheduled_dependencies(
                    task_id TEXT NOT NULL REFERENCES scheduled_tasks(task_id),
                    depends_on TEXT NOT NULL REFERENCES scheduled_tasks(task_id),
                    PRIMARY KEY(task_id, depends_on));
                CREATE TABLE IF NOT EXISTS scheduler_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES scheduled_tasks(task_id),
                    operation TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS scheduled_status_idx
                    ON scheduled_tasks(status, sequence);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _row(db, task_id):
        row = db.execute('SELECT * FROM scheduled_tasks WHERE task_id=?',
                         (task_id,)).fetchone()
        if row is None:
            raise SchedulerError('unknown scheduled task: ' + str(task_id))
        return row

    @staticmethod
    def _decode(db, row, *, include_token=False):
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        value['result'] = json.loads(value['result']) if value['result'] is not None else None
        value['depends_on'] = [dep[0] for dep in db.execute(
            'SELECT depends_on FROM scheduled_dependencies WHERE task_id=? ORDER BY depends_on',
            (value['task_id'],)).fetchall()]
        if not include_token:
            value.pop('claim_token', None)
        return value

    @staticmethod
    def _event(db, task_id, operation, detail):
        db.execute('INSERT INTO scheduler_events(task_id,operation,detail,created_at) VALUES(?,?,?,?)',
                   (task_id, operation, _json(detail), _now()))

    def enqueue(self, task_id, payload, depends_on=None):
        task_id = _text(task_id, 'task_id')
        if not isinstance(payload, dict):
            raise SchedulerError('payload must be a JSON object')
        encoded = _json(payload)
        dependencies = [] if depends_on is None else depends_on
        if (not isinstance(dependencies, list)
                or any(not isinstance(dep, str) or not dep.strip() for dep in dependencies)):
            raise SchedulerError('depends_on must be a list of non-empty task IDs')
        dependencies = [dep.strip() for dep in dependencies]
        if len(set(dependencies)) != len(dependencies) or task_id in dependencies:
            raise SchedulerError('duplicate or self dependency')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM scheduled_tasks WHERE task_id=?', (task_id,)).fetchone():
                raise SchedulerError('task already exists; enqueue cannot overwrite history')
            for dep in dependencies:
                self._row(db, dep)
            now = _now()
            db.execute('''INSERT INTO scheduled_tasks(task_id,payload,status,created_at,updated_at)
                          VALUES(?,?,'PENDING',?,?)''', (task_id, encoded, now, now))
            db.executemany('INSERT INTO scheduled_dependencies(task_id,depends_on) VALUES(?,?)',
                           [(task_id, dep) for dep in dependencies])
            self._event(db, task_id, 'ENQUEUED', {'depends_on': dependencies})
            return self._decode(db, self._row(db, task_id))

    def claim(self, worker_id):
        worker_id = _text(worker_id, 'worker_id')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT task.* FROM scheduled_tasks task
                WHERE task.status='PENDING'
                AND NOT EXISTS(
                    SELECT 1 FROM scheduled_dependencies dep
                    JOIN scheduled_tasks parent ON parent.task_id=dep.depends_on
                    WHERE dep.task_id=task.task_id AND parent.status!='COMPLETED')
                ORDER BY task.sequence LIMIT 1''').fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE scheduled_tasks SET status='RUNNING',worker_id=?,claim_token=?,updated_at=? WHERE task_id=? AND status='PENDING'",
                       (worker_id, token, _now(), row['task_id']))
            self._event(db, row['task_id'], 'CLAIMED', {'worker_id': worker_id})
            return self._decode(db, self._row(db, row['task_id']), include_token=True)

    @staticmethod
    def _owner(row, token):
        if (not isinstance(token, str) or not token
                or not isinstance(row['claim_token'], str)
                or not secrets.compare_digest(row['claim_token'], token)):
            raise SchedulerError('WORKER_TOKEN_MISMATCH')

    def complete(self, task_id, token, result):
        """Persist one worker result; only explicit COMPLETED unlocks children.

        Failed, unverified, paused and exhausted runtime results become FAILED;
        UNKNOWN and NEEDS_RECONCILIATION become UNKNOWN. Neither is retried.
        Exact repeats are idempotent; a different result cannot rewrite history.
        """
        if not isinstance(result, dict):
            raise SchedulerError('result must be a JSON object')
        runtime_status = _text(result.get('status'), 'result.status')
        encoded = _json(result)
        if runtime_status == 'COMPLETED':
            status = 'COMPLETED'
        elif runtime_status in {'UNKNOWN', 'NEEDS_RECONCILIATION', 'LEASE_LOST'}:
            status = 'UNKNOWN'
        elif runtime_status in {'FAILED', 'MODEL_ERROR', 'BUDGET_EXHAUSTED', 'CONTROL_BLOCKED',
                                'UNVERIFIED', 'PAUSED', 'CANCELLED'}:
            status = 'FAILED'
        else:
            raise SchedulerError('unrecognized runtime result status')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, task_id)
            self._owner(row, token)
            if row['status'] != 'RUNNING':
                if row['result'] == encoded:
                    return self._decode(db, row)
                raise SchedulerError('RESULT_CONFLICT: task is not running')
            db.execute('UPDATE scheduled_tasks SET status=?,result=?,updated_at=? WHERE task_id=?',
                       (status, encoded, _now(), task_id))
            self._event(db, task_id, 'RESULT_RECORDED',
                        {'worker_id': row['worker_id'], 'status': status, 'runtime_status': runtime_status})
            return self._decode(db, self._row(db, task_id))

    def cancel(self, task_id, *, reason, token=None, stop_proof=None):
        reason = _text(reason, 'reason')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, task_id)
            if row['status'] == 'CANCELLED':
                return self._decode(db, row)
            if row['status'] == 'RUNNING':
                self._owner(row, token)
                if (not isinstance(stop_proof, dict)
                        or stop_proof.get('process_stopped') is not True
                        or stop_proof.get('task_id') != task_id):
                    raise SchedulerError('RUNNING_CANCEL_REQUIRES_BOUND_STOP_PROOF')
            elif row['status'] != 'PENDING':
                raise SchedulerError('terminal result cannot be rewritten by cancel')
            result = {'status': 'CANCELLED', 'reason': reason}
            db.execute("UPDATE scheduled_tasks SET status='CANCELLED',result=?,updated_at=? WHERE task_id=?",
                       (_json(result), _now(), task_id))
            self._event(db, task_id, 'CANCELLED', {'reason': reason, 'worker_id': row['worker_id']})
            return self._decode(db, self._row(db, task_id))

    def get(self, task_id):
        with self._db() as db:
            return self._decode(db, self._row(db, task_id))

    def list_tasks(self):
        with self._db() as db:
            return [self._decode(db, row) for row in db.execute(
                'SELECT * FROM scheduled_tasks ORDER BY sequence').fetchall()]

    def events(self, task_id):
        with self._db() as db:
            self._row(db, task_id)
            rows = db.execute('SELECT * FROM scheduler_events WHERE task_id=? ORDER BY id',
                              (task_id,)).fetchall()
            return [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows]

    def run_batch(self, worker_id, handler, *, max_tasks=1):
        """Run at most max_tasks synchronous host callbacks, without a daemon.

        An exception may have followed a side effect, so it becomes UNKNOWN.
        Only the host-supplied callback executes anything; stored JSON is data.
        """
        if (isinstance(max_tasks, bool) or not isinstance(max_tasks, int)
                or not 1 <= max_tasks <= 1000):
            raise SchedulerError('max_tasks must be an integer from 1 to 1000')
        if not callable(handler):
            raise SchedulerError('handler must be callable')
        worker_id = _text(worker_id, 'worker_id')
        results = []
        for _ in range(max_tasks):
            claimed = self.claim(worker_id)
            if claimed is None:
                break
            try:
                result = handler(deepcopy(claimed))
                if not isinstance(result, dict):
                    raise SchedulerError('handler result must be an object')
                _json(result)
                if result.get('status') not in {'COMPLETED', 'UNKNOWN', 'NEEDS_RECONCILIATION', 'LEASE_LOST',
                        'FAILED', 'MODEL_ERROR', 'BUDGET_EXHAUSTED', 'CONTROL_BLOCKED',
                        'UNVERIFIED', 'PAUSED', 'CANCELLED'}:
                    raise SchedulerError('handler result status is not recognized')
            except Exception as exc:
                result = {'status': 'NEEDS_RECONCILIATION',
                          'error': type(exc).__name__ + ': ' + str(exc)}
            results.append(self.complete(claimed['task_id'], claimed['claim_token'], result))
        return results
