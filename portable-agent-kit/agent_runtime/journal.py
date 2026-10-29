"""Durable model-loop checkpoints with optimistic single-writer updates.

An unfinished action is never replayed automatically after restart. A host must
reconcile its effect first. Store this database outside the tool workspace.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from control_plane.contracts import canonical_hash


class JournalError(ValueError):
    pass


class RunJournal:
    def __init__(self,database):
        self.database=Path(database)
        self.database.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_checkpoints(task_id TEXT PRIMARY KEY,binding TEXT NOT NULL,generation INTEGER NOT NULL,snapshot TEXT NOT NULL)')

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
