#!/usr/bin/env python3
"""Local artifact session: init, next, check, finish. Python standard library.

The host owns the contract/session. No arbitrary evidence import, tool execution,
external actions, model calls or scheduler. Not a sandbox against its owner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.local_checks import artifact, canonical, load_json, run_check, verifier
from core.state_machine import ProtocolError, apply, digest, start


@contextmanager
def locked(directory):
    lock = directory / ".lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("session locked; inspect the existing writer before recovery") from None
    try:
        yield
    finally:
        lock.rmdir()


def save(directory, record):
    target = directory / "session.json"
    temp = directory / (".session-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(canonical(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def send(record, op, payload=None):
    state = record["state"]
    key = uuid.uuid4().hex
    request = {"protocol": state["protocol"], "request_id": key,
               "op": op, "idempotency_key": key, "task_id": state["task_id"],
               "revision": state["revision"], "contract_hash": state["contract_hash"],
               "candidate": state["candidate"], "payload": payload or {}}
    record["state"], response = apply(state, request, verify_evidence=verifier(
        record["workspace"], record["contract"], record["inputs"]))
    return response


def next_step(record):
    state, contract = record["state"], record["contract"]
    verify = verifier(record["workspace"], contract, record["inputs"])
    if state["status"] == "COMPLETED":
        current = {x.get("check_id"): x for x in state["evidence"]}
        if any(c["id"] not in current or not verify(current[c["id"]]) for c in contract["acceptance"]):
            return {"status": "STALE_COMPLETION", "historical_status": "COMPLETED",
                    "next_action": "RESTORE_CANDIDATE_OR_OPEN_REVISION"}
    if state["status"] in ("COMPLETED", "CANCELLED", "PAUSED"):
        return {"status": state["status"], "next_action": state["next_action"]}
    try:
        changed = [x["path"] for x in record["inputs"] if artifact(record["workspace"], x["path"]) != x]
    except (OSError, ValueError) as exc:
        return {"status": "BLOCKED", "next_action": "RESTORE_APPROVED_INPUTS", "reason": str(exc)}
    if changed:
        return {"status": "BLOCKED", "next_action": "RESTORE_OR_REAUTHORIZE_INPUTS", "changed": changed}
    if state.get("unknown_tool_calls") or state.get("open_findings"):
        return {"status": "BLOCKED", "next_action": "HOST_RECONCILIATION_REQUIRED"}
    for check in contract["acceptance"]:
        history = [x for x in state["evidence"] if x.get("check_id") == check["id"]]
        current = history[-1] if history else None
        if current and current["verdict"] == "PASS" and verify(current):
            continue
        failures = sum(x["verdict"] == "FAIL" for x in history)
        return {"status": "RUNNING", "next_action": "ESCALATE_OR_REPLAN" if failures >= 2 else "COMPLETE_ONE_CHECK",
                "check": check, "failed_observations": failures,
                "instruction": "Perform one bounded repair, then run check. Repeated failure triggers diagnosis/smaller steps or a capable reviewer; no blind retries or automatic spend."}
    return {"status": "READY_FOR_FINISH", "next_action": "FINISH",
            "limits": "Only contract-defined local artifact checks; domain semantics, UX, external effects and independent review need their real host checks."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("op", choices=("init", "next", "check", "finish"))
    parser.add_argument("--session", type=Path, required=True, help="control directory outside workspace")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--check")
    args = parser.parse_args(argv)
    directory = args.session.resolve()
    if args.op == "init":
        if args.workspace is None or args.contract is None:
            raise ValueError("init needs --workspace and --contract")
        workspace = args.workspace.resolve(strict=True)
        if directory == workspace or workspace in directory.parents:
            raise ValueError("session must be outside the deliverable workspace")
        contract = load_json(args.contract.read_text(encoding="utf-8"))
        if contract.get("policy", {}).get("external_actions"):
            raise ValueError("local runner cannot execute or verify external actions")
        probes = [c.get("probe", {}) for c in contract.get("acceptance", [])]
        pinned = contract.get("inputs", [])
        if not isinstance(pinned, list) or not all(isinstance(p, str) and p for p in pinned):
            raise ValueError("inputs must be a list of workspace-relative paths")
        if set(pinned) & set(contract.get("scope", [])):
            raise ValueError("approved inputs and output scope must be distinct")
        if any(p.get("source") not in pinned for p in probes if p.get("type") == "csv.reconcile"):
            raise ValueError("CSV source must be pinned in inputs")
        inputs = [artifact(workspace, p) for p in pinned]
        caps = {"fs.read": True, "fs.write": False, "shell.exec": False,
                "evidence.hash": True, "checkpoint.persist": True}
        state = start(contract, caps, adapter_id="local-artifact-runner", host_id="local-python")
        if state["status"] != "READY":
            raise ValueError("local capability missing: " + canonical(state["blockers"]))
        directory.mkdir(parents=True, exist_ok=True)
        with locked(directory):
            if (directory / "session.json").exists():
                raise ValueError("session exists; read next instead of resetting history")
            record = {"format": "local-artifact-session/1", "workspace": str(workspace),
                      "contract": contract, "inputs": inputs, "state": state}
            send(record, "start")
            save(directory, record)
        return next_step(record)
    with locked(directory):
        record = load_json((directory / "session.json").read_text(encoding="utf-8"))
        if digest(record["contract"]) != record["state"]["contract_hash"]:
            raise ValueError("session contract changed; preserve history and open an authorized revision")
        if args.op == "next":
            return next_step(record)
        if args.op == "check":
            check = next((c for c in record["contract"]["acceptance"] if c["id"] == args.check), None)
            if check is None:
                raise ValueError("--check must name a contract acceptance ID")
            observed = run_check(record["workspace"], check, record["contract"]["scope"])
            state = record["state"]
            payload = {"id": "E-" + uuid.uuid4().hex, "kind": "local-check", "check_id": check["id"],
                       "environment": check["environment"], "uri": check.get("probe", {}).get("path", "unavailable"),
                       "sha256": observed["artifacts"][0]["sha256"] if observed["artifacts"] else "0" * 64,
                       "claim": check["expected"], **observed,
                       **{k: state[k] for k in ("task_id", "revision", "contract_hash", "candidate")}}
            response = send(record, "evidence", payload)
            save(directory, record)
            return {"check_id": check["id"], **observed, "state": response, "next": next_step(record)}
        send(record, "phase", {"phase": "verify"})
        response = send(record, "finish")
        save(directory, record)
        return response


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except (ValueError, OSError, ProtocolError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
