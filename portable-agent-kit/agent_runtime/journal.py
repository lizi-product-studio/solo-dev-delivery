"""Durable model-loop checkpoints with optimistic single-writer updates.

An unfinished action is never replayed automatically after restart. A host must
reconcile its effect first. Store this database outside the tool workspace.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
import uuid

from control_plane.contracts import canonical_hash


class JournalError(ValueError):
    pass


class RunJournal:
    def __init__(self,database):
        self.database=Path(database)
        self.database.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_checkpoints(task_id TEXT PRIMARY KEY,binding TEXT NOT NULL,generation INTEGER NOT NULL,snapshot TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS agent_control_requests(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                request_id TEXT NOT NULL, operation TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, acknowledged_at REAL,
                result TEXT, UNIQUE(task_id,request_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS agent_control_pending ON agent_control_requests(task_id,status,sequence)')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.database,timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback();raise
        finally:
            db.close()

    def load(self,task_id):
        with self._db() as db:
            row=db.execute('SELECT binding,generation,snapshot FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone()
        if row is None:return None
        return {'binding':row[0],'generation':row[1],'snapshot':json.loads(row[2])}

    def create(self,task_id,binding,snapshot):
        with self._db() as db:
            try:
                db.execute('INSERT INTO agent_checkpoints VALUES(?,?,0,?)',
                           (task_id,canonical_hash(binding),json.dumps(snapshot,ensure_ascii=False,allow_nan=False)))
            except sqlite3.IntegrityError:
                raise JournalError('run already exists; use explicit resume') from None
        return self.load(task_id)

    def check_binding(self,task_id,binding):
        saved=self.load(task_id)
        if saved is None:raise JournalError('run checkpoint does not exist')
        if saved['binding']!=canonical_hash(binding):raise JournalError('STALE_RUN_BINDING')
        return saved

    def save(self,task_id,expected_generation,snapshot):
        with self._db() as db:
            updated=db.execute('UPDATE agent_checkpoints SET generation=generation+1,snapshot=? WHERE task_id=? AND generation=?',
                               (json.dumps(snapshot,ensure_ascii=False,allow_nan=False),task_id,expected_generation))
            if updated.rowcount!=1:raise JournalError('CONCURRENT_CHECKPOINT_UPDATE')
        return self.load(task_id)

    @staticmethod
    def _control_row(row):
        if row is None:return None
        value=dict(zip(('sequence','task_id','request_id','operation','reason','status',
                        'created_at','acknowledged_at','result'),row))
        if value['result'] is not None:value['result']=json.loads(value['result'])
        return value

    @staticmethod
    def _control_text(value,name,limit):
        if not isinstance(value,str) or not value.strip() or len(value)>limit:
            raise JournalError(name+' must be a nonempty bounded string')
        return value

    def request_control(self,task_id,operation,reason,*,request_id=None):
        """Request a cooperative stop; acceptance is not a process-stop proof.

        Requests do not change checkpoint generation and cannot make an unknown
        action safe. The live Runtime acknowledges at a known safe boundary.
        """
        task_id=self._control_text(task_id,'task_id',1024)
        if operation not in ('pause','cancel'):raise JournalError('control operation must be pause or cancel')
        reason=self._control_text(reason,'reason',8192)
        request_id=self._control_text(uuid.uuid4().hex if request_id is None else request_id,'request_id',256)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            checkpoint=db.execute('SELECT snapshot FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone()
            if checkpoint is None:raise JournalError('run checkpoint does not exist')
            existing=db.execute('SELECT * FROM agent_control_requests WHERE task_id=? AND request_id=?',
                                (task_id,request_id)).fetchone()
            if existing is not None:
                value=self._control_row(existing)
                if (value['operation'],value['reason'])!=(operation,reason):
                    raise JournalError('CONTROL_REQUEST_CONFLICT')
                return value
            snapshot=json.loads(checkpoint[0])
            if snapshot['phase'] in {'FINISHED','CANCELLED'}:
                raise JournalError('run is terminal; control request not accepted')
            if snapshot.get('controls_closed') is True:
                raise JournalError('FINALIZING: control request not accepted; inspect final outcome')
            db.execute('''INSERT INTO agent_control_requests
                (task_id,request_id,operation,reason,status,created_at) VALUES(?,?,?,?,?,?)''',
                (task_id,request_id,operation,reason,'REQUESTED',time.time()))
            return self._control_row(db.execute('SELECT * FROM agent_control_requests WHERE task_id=? AND request_id=?',
                                               (task_id,request_id)).fetchone())

    def pending_control(self,task_id):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone():
                raise JournalError('run checkpoint does not exist')
            row=db.execute('''SELECT * FROM agent_control_requests WHERE task_id=? AND status='REQUESTED'
                ORDER BY CASE operation WHEN 'cancel' THEN 0 ELSE 1 END,sequence LIMIT 1''',(task_id,)).fetchone()
            return self._control_row(row)

    def control_requests(self,task_id):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone():
                raise JournalError('run checkpoint does not exist')
            return [self._control_row(row) for row in db.execute(
                'SELECT * FROM agent_control_requests WHERE task_id=? ORDER BY sequence',(task_id,)).fetchall()]

    def acknowledge_control(self,task_id,request_id,expected_generation,snapshot):
        """Atomically save a stopped checkpoint and acknowledge one request.

        Only trusted Runtime code supplies this snapshot, after in-flight work
        returned. A concurrent checkpoint writer cannot acknowledge its stop.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM agent_control_requests WHERE task_id=? AND request_id=?',
                           (task_id,request_id)).fetchone()
            request=self._control_row(row)
            if request is None:raise JournalError('unknown control request')
            if request['status'] in {'ACKNOWLEDGED','SUPERSEDED'}:return self._load_db(db,task_id)
            if request['status']!='REQUESTED':raise JournalError('control request is not pending')
            phase='PAUSED' if request['operation']=='pause' else 'CANCELLED'
            superseded=request['operation']=='pause' and snapshot.get('phase')=='CANCELLED'
            if superseded:phase='CANCELLED'
            if snapshot.get('phase')!=phase or snapshot.get('pending') is not None:
                raise JournalError('CONTROL_ACK_REQUIRES_STOPPED_CHECKPOINT')
            if snapshot.get('last_status')!=phase:raise JournalError('control result does not match request')
            encoded=json.dumps(snapshot,ensure_ascii=False,allow_nan=False)
            changed=db.execute('UPDATE agent_checkpoints SET generation=generation+1,snapshot=? WHERE task_id=? AND generation=?',
                               (encoded,task_id,expected_generation))
            if changed.rowcount!=1:raise JournalError('CONCURRENT_CHECKPOINT_UPDATE')
            result=json.dumps({'status':phase,'next_step':snapshot['next_step']})
            db.execute("UPDATE agent_control_requests SET status=?,acknowledged_at=?,result=? WHERE task_id=? AND request_id=?",
                       ('SUPERSEDED' if superseded else 'ACKNOWLEDGED',time.time(),result,task_id,request_id))
            if phase=='CANCELLED':
                db.execute("UPDATE agent_control_requests SET status='SUPERSEDED',acknowledged_at=?,result=? WHERE task_id=? AND status='REQUESTED'",
                           (time.time(),result,task_id))
            return self._load_db(db,task_id)

    @staticmethod
    def _load_db(db,task_id):
        row=db.execute('SELECT binding,generation,snapshot FROM agent_checkpoints WHERE task_id=?',(task_id,)).fetchone()
        if row is None:raise JournalError('run checkpoint does not exist')
        return {'binding':row[0],'generation':row[1],'snapshot':json.loads(row[2])}

    def prepare_finish(self,task_id,expected_generation,snapshot):
        """Close control acceptance atomically just before the completion commit.

        Existing stop requests win; later requests are explicitly rejected as
        too late, rather than being accepted and silently overwritten by finish.
        """
        if snapshot.get('phase')!='FINISH_PENDING':raise JournalError('finish checkpoint required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM agent_control_requests WHERE task_id=? AND status='REQUESTED'",(task_id,)).fetchone():
                raise JournalError('CONTROL_REQUEST_PENDING')
            snapshot=deepcopy(snapshot);snapshot['controls_closed']=True
            changed=db.execute('UPDATE agent_checkpoints SET generation=generation+1,snapshot=? WHERE task_id=? AND generation=?',
                               (json.dumps(snapshot,ensure_ascii=False,allow_nan=False),task_id,expected_generation))
            if changed.rowcount!=1:raise JournalError('CONCURRENT_CHECKPOINT_UPDATE')
            return self._load_db(db,task_id)

    def prepare_action(self,task_id,expected_generation,snapshot):
        """Atomically choose between an accepted stop and tool dispatch.

        A request committed before this claim wins. Requests arriving after the
        ACTION_PENDING claim wait for the in-flight action to return safely.
        """
        if snapshot.get('phase')!='ACTION_PENDING' or not isinstance(snapshot.get('pending'),dict):
            raise JournalError('action checkpoint required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM agent_control_requests WHERE task_id=? AND status='REQUESTED'",(task_id,)).fetchone():
                raise JournalError('CONTROL_REQUEST_PENDING')
            changed=db.execute('UPDATE agent_checkpoints SET generation=generation+1,snapshot=? WHERE task_id=? AND generation=?',
                               (json.dumps(snapshot,ensure_ascii=False,allow_nan=False),task_id,expected_generation))
            if changed.rowcount!=1:raise JournalError('CONCURRENT_CHECKPOINT_UPDATE')
            return self._load_db(db,task_id)

    def reconcile(self,task_id,*,action_id,observation,verify_readback):
        saved=self.load(task_id)
        if saved is None:raise JournalError('run checkpoint does not exist')
        snapshot=saved['snapshot'];pending=snapshot.get('pending')
        if snapshot.get('phase')!='ACTION_PENDING' or not pending or pending['id']!=action_id:
            raise JournalError('no matching unknown action')
        # The callback is supplied by trusted host code, never by model output.
        if not callable(verify_readback) or verify_readback(deepcopy(pending),deepcopy(observation)) is not True:
            raise JournalError('READBACK_NOT_VERIFIED')
        snapshot['events'][-1]['result']=deepcopy(observation)
        snapshot['pending']=None;snapshot['phase']='READY'
        snapshot['next_step']=snapshot['events'][-1]['step']+1
        snapshot.setdefault('reconciliations',[]).append({'action_id':action_id,'observation':deepcopy(observation)})
        return self.save(task_id,saved['generation'],snapshot)
