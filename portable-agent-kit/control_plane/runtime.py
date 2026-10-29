"""SQLite-backed orchestration around the provider-neutral state machine."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ContractError, normalize_contract
from .evaluator import make_verifier
from core.state_machine import ProtocolError, apply, start


FORMAT = "control-plane/1"


class ControlPlaneError(ValueError):
    """Raised when a runtime operation would violate ownership or state gates."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


class DeliveryRuntime:
    """Small host-neutral runtime with persistence, leases and event history.

    It intentionally performs no arbitrary tool execution.  A host proposes or
    executes a tool action and submits the result through ``apply``; this keeps
    permissions and identity in the host while making state, ownership,
    idempotency and evidence gates durable outside the model process.
    """

    def __init__(self, database, *, evidence_root=None):
        self.database = Path(database).resolve()
        self.evidence_root = Path(evidence_root).resolve() if evidence_root else None
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(str(self.database), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self):
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                contract_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                candidate TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                lease_id TEXT,
                operation TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leases (
                task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
                lease_id TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_task_idx ON events(task_id, event_id);
            """)

    def _row(self, db, task_id):
        row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ControlPlaneError("unknown task: " + str(task_id))
        return row

    def _state(self, row):
        return json.loads(row["state_json"])

    def create_task(self, raw_contract, capabilities, *, adapter_id="runtime",
                    host_id="local"):
        try:
            contract = normalize_contract(raw_contract)
        except ContractError as exc:
            raise ControlPlaneError(str(exc)) from None
        state = start(contract, capabilities, adapter_id=adapter_id, host_id=host_id)
        state["control_plane"] = {
            "steps_used": 0,
            "max_steps": contract["budget"]["max_steps"],
            "max_retries": contract["budget"]["max_retries"],
        }
        now = _now()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tasks WHERE task_id = ?",
                          (state["task_id"],)).fetchone():
                raise ControlPlaneError("task already exists; use load/plan instead of resetting it")
            db.execute("""INSERT INTO tasks
                (task_id, contract_json, state_json, status, revision, candidate, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (state["task_id"], _dump(contract), _dump(state), state["status"],
                        state["revision"], state["candidate"], now, now))
            db.execute("""INSERT INTO events
                (task_id, lease_id, operation, request_json, response_json, created_at)
                VALUES (?, NULL, ?, ?, ?, ?)""",
                       (state["task_id"], "create", _dump({"contract": contract}),
                        _dump({"status": state["status"], "revision": state["revision"]}), now))
        return deepcopy(state)

    def get(self, task_id):
        with self._db() as db:
            return deepcopy(self._state(self._row(db, task_id)))

    def contract(self, task_id):
        with self._db() as db:
            return json.loads(self._row(db, task_id)["contract_json"])

    def list_tasks(self):
        with self._db() as db:
            rows = db.execute("SELECT task_id, status, revision, candidate, updated_at FROM tasks ORDER BY updated_at").fetchall()
            return [dict(row) for row in rows]

    def _require_lease(self, db, task_id, lease_id):
        if not isinstance(lease_id, str) or not lease_id.strip():
            raise ControlPlaneError("mutating operation requires a lease_id")
        row = db.execute("SELECT * FROM leases WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ControlPlaneError("no active lease; claim the task first")
        if row["lease_id"] != lease_id:
            raise ControlPlaneError("LEASE_OWNER_MISMATCH")
        if float(row["expires_at"]) <= time.time():
            raise ControlPlaneError("LEASE_EXPIRED; reconcile before continuing")
        return row

    def claim(self, task_id, owner, *, ttl_seconds=300):
        if not isinstance(owner, str) or not owner.strip():
            raise ControlPlaneError("owner must be a non-empty string")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ControlPlaneError("ttl_seconds must be a positive integer")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, task_id)
            current = db.execute("SELECT * FROM leases WHERE task_id = ?", (task_id,)).fetchone()
            if current and float(current["expires_at"]) > time.time():
                raise ControlPlaneError("TASK_ALREADY_LEASED")
            lease_id = "lease-" + uuid.uuid4().hex
            now = time.time()
            db.execute("INSERT OR REPLACE INTO leases(task_id, lease_id, owner, acquired_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                       (task_id, lease_id, owner.strip(), _now(), now + ttl_seconds))
        return {"format": FORMAT, "status": "CLAIMED", "task_id": task_id,
                "lease_id": lease_id, "owner": owner.strip(),
                "expires_at": now + ttl_seconds}

    def release(self, task_id, lease_id):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, task_id, lease_id)
            db.execute("DELETE FROM leases WHERE task_id = ?", (task_id,))
        return {"format": FORMAT, "status": "RELEASED", "task_id": task_id,
                "lease_id": lease_id}

    def heartbeat(self, task_id, lease_id, *, ttl_seconds=300):
        """Extend a live lease without changing task state or consuming budget."""
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ControlPlaneError("ttl_seconds must be a positive integer")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, task_id, lease_id)
            expires_at = time.time() + ttl_seconds
            db.execute("UPDATE leases SET expires_at = ? WHERE task_id = ?",
                       (expires_at, task_id))
        return {"format": FORMAT, "status": "HEARTBEAT", "task_id": task_id,
                "lease_id": lease_id, "expires_at": expires_at}

    def reclaim(self, task_id, owner, *, stop_proof, ttl_seconds=300):
        """Reclaim an expired lease only after the host proves the writer stopped."""
        if not isinstance(stop_proof, dict) or stop_proof.get("process_stopped") is not True:
            raise ControlPlaneError("RECLAIM_REQUIRES_PROCESS_STOP_PROOF")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, task_id)
            lease = db.execute("SELECT * FROM leases WHERE task_id = ?", (task_id,)).fetchone()
            if lease is None:
                raise ControlPlaneError("NO_EXPIRED_LEASE")
            if float(lease["expires_at"]) > time.time():
                raise ControlPlaneError("LEASE_STILL_ACTIVE")
            db.execute("DELETE FROM leases WHERE task_id = ?", (task_id,))
        result = self.claim(task_id, owner, ttl_seconds=ttl_seconds)
        result["reclaimed_from"] = lease["owner"]
        result["stop_proof"] = deepcopy(stop_proof)
        return result

    def recover(self, task_id):
        """Return a recovery decision; never silently steal an active writer."""
        with self._db() as db:
            row = self._row(db, task_id)
            lease = db.execute("SELECT * FROM leases WHERE task_id = ?", (task_id,)).fetchone()
            state = self._state(row)
            if lease is None:
                return {"format": FORMAT, "status": "NO_ACTIVE_LEASE", "task_id": task_id,
                        "state": state}
            if float(lease["expires_at"]) > time.time():
                return {"format": FORMAT, "status": "ACTIVE_WRITER", "task_id": task_id,
                        "owner": lease["owner"], "lease_id": lease["lease_id"],
                        "expires_at": lease["expires_at"]}
            return {"format": FORMAT, "status": "RECOVERY_REQUIRED", "task_id": task_id,
                    "owner": lease["owner"], "lease_id": lease["lease_id"],
                    "expired_at": lease["expires_at"], "state": state,
                    "next_action": "verify_process_stopped_then_reclaim"}

    def restart(self, task_id):
        """Reload a persisted task and return a restart-safe plan."""
        state = self.get(task_id)
        return {"format": FORMAT, "status": "RESTARTED", "task_id": task_id,
                "revision": state["revision"], "state": state,
                "next_action": state.get("next_action"),
                "lease": self.recover(task_id)}

    def cancel(self, task_id, lease_id, *, executor_stopped=False, reason=""):
        if not reason.strip():
            raise ControlPlaneError("cancel reason is required")
        return self.apply(task_id, lease_id, "cancel",
                          {"explicit": True, "executor_stopped": executor_stopped,
                           "reason": reason})

    def reconcile_external(self, task_id, lease_id, *, tool_call_id, evidence,
                           outcome, evidence_root=None, reviewer_id=None):
        if outcome not in ("SUCCEEDED", "FAILED"):
            raise ControlPlaneError("invalid external outcome")
        return self.apply(task_id, lease_id, "reconcile",
                          {"tool_call_id": tool_call_id,
                           "evidence_id": evidence.get("id"),
                           "observed_outcome": outcome},
                          evidence_root=evidence_root, reviewer_id=reviewer_id)

    def plan(self, task_id):
        state = self.get(task_id)
        return {"format": FORMAT, "task_id": task_id, "revision": state["revision"],
                "status": state["status"], "phase": state["phase"],
                "next_action": state.get("next_action"),
                "blockers": deepcopy(state.get("blockers", [])),
                "unknown_tool_calls": deepcopy(state.get("unknown_tool_calls", [])),
                "budget": deepcopy(state.get("contract", {}).get("budget", {})),
                "limits": "One bounded action per dispatch; host owns tool execution and scheduling."}

    def apply(self, task_id, lease_id, operation, payload=None, *, request_id=None,
              verify_evidence=None, evidence_root=None, reviewer_id=None):
        payload = payload or {}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            lease = self._require_lease(db, task_id, lease_id)
            row = self._row(db, task_id)
            state = self._state(row)
            contract = json.loads(row["contract_json"])
            request_id = request_id or uuid.uuid4().hex
            is_replay = request_id in state.get("idempotency", {})
            control = state.setdefault("control_plane", {
                "steps_used": 0,
                "max_steps": contract.get("budget", {}).get("max_steps", 20),
                "max_retries": contract.get("budget", {}).get("max_retries", 2),
            })
            if operation not in {"observe", "checkpoint", "phase"} and not is_replay:
                if control["steps_used"] >= control["max_steps"]:
                    raise ControlPlaneError("BUDGET_EXHAUSTED")
            request = {"protocol": state["protocol"], "request_id": request_id,
                       "op": operation, "task_id": task_id,
                       "revision": state["revision"],
                       "contract_hash": state["contract_hash"],
                       "candidate": state["candidate"],
                       "idempotency_key": request_id, "payload": deepcopy(payload)}
            if verify_evidence is None and (evidence_root or self.evidence_root) and reviewer_id:
                verify_evidence = make_verifier(contract, evidence_root or self.evidence_root,
                                                reviewer_id=reviewer_id)
            try:
                new_state, response = apply(state, request, verify_evidence=verify_evidence)
            except (ProtocolError, ValueError) as exc:
                raise ControlPlaneError(str(exc)) from None
            now = _now()
            if operation not in {"observe", "checkpoint", "phase"} and not is_replay:
                new_state.setdefault("control_plane", control)["steps_used"] = control["steps_used"] + 1
            db.execute("UPDATE tasks SET state_json = ?, status = ?, revision = ?, updated_at = ? WHERE task_id = ?",
                       (_dump(new_state), new_state["status"], new_state["revision"], now, task_id))
            db.execute("""INSERT INTO events
                (task_id, lease_id, operation, request_json, response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                       (task_id, lease["lease_id"], operation, _dump(request), _dump(response), now))
        return deepcopy(new_state), deepcopy(response)

    def events(self, task_id):
        with self._db() as db:
            self._row(db, task_id)
            rows = db.execute("SELECT * FROM events WHERE task_id = ? ORDER BY event_id", (task_id,)).fetchall()
            return [{"event_id": row["event_id"], "task_id": row["task_id"],
                     "lease_id": row["lease_id"], "operation": row["operation"],
                     "request": json.loads(row["request_json"]),
                     "response": json.loads(row["response_json"]),
                     "created_at": row["created_at"]} for row in rows]
