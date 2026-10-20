"""Small provider-neutral delivery state machine.

This module deliberately has no model, network, filesystem, Git, browser or
vendor imports. A host adapter supplies capabilities, tool results and durable
storage. The model is allowed to propose the next action, while this module
decides whether that action is legal and whether delivery is blocked.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

PROTOCOL = "aap/0.1"
PHASES = ("assess", "work", "verify", "review", "deliver")
STATUSES = ("READY", "RUNNING", "PAUSED", "BLOCKED", "UNKNOWN", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT")


class ProtocolError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ProtocolError(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evidence_ref(value, *, task_id, revision, contract_hash, candidate, producer):
    """Validate a portable evidence reference without trusting its claim."""
    _require(isinstance(value, dict), "evidence must be an object")
    for key in ("id", "kind", "uri", "sha256", "claim", "verdict"):
        _require(isinstance(value.get(key), str) and value[key].strip(), "evidence missing " + key)
    _require(value["verdict"] in ("PASS", "FAIL", "UNKNOWN"), "invalid evidence verdict")
    _require(len(value["sha256"]) == 64, "evidence sha256 must be a hex digest")
    _require(value.get("task_id") == task_id, "evidence task mismatch")
    _require(value.get("revision") == revision, "evidence revision mismatch")
    _require(value.get("contract_hash") == contract_hash, "evidence contract mismatch")
    _require(value.get("candidate") == candidate, "evidence candidate mismatch")
    value = deepcopy(value)
    value.setdefault("producer", producer)
    value.setdefault("observed_at", _now())
    return value


def validate_contract(contract):
    _require(isinstance(contract, dict), "contract must be an object")
    for key in ("id", "goal", "owner", "authorization_ref", "candidate"):
        _require(isinstance(contract.get(key), str) and contract[key].strip(), "contract missing " + key)
    for key in ("scope", "excluded", "acceptance"):
        _require(isinstance(contract.get(key), list) and contract[key], "contract " + key + " must be non-empty")
    ids = set()
    for row in contract["acceptance"]:
        _require(isinstance(row, dict), "acceptance item must be an object")
        for key in ("id", "expected", "environment"):
            _require(isinstance(row.get(key), str) and row[key].strip(), "acceptance missing " + key)
        _require(row["id"] not in ids, "duplicate acceptance id")
        ids.add(row["id"])
    policy = contract.get("policy", {})
    _require(isinstance(policy, dict), "policy must be an object")
    _require(isinstance(policy.get("external_actions", False), bool), "policy.external_actions must be boolean")
    return deepcopy(contract)


def probe(capabilities):
    """Return a three-state capability report; unknown is never treated as true."""
    _require(isinstance(capabilities, dict), "capabilities must be an object")
    result = {}
    for name, value in capabilities.items():
        if isinstance(value, bool):
            result[name] = "supported" if value else "unsupported"
        elif value in ("supported", "unsupported", "unknown"):
            result[name] = value
        elif isinstance(value, dict) and value.get("supported") in (True, False, "unknown"):
            raw = value["supported"]
            result[name] = "unknown" if raw == "unknown" else ("supported" if raw else "unsupported")
        else:
            result[name] = "unknown"
    return {"protocol": PROTOCOL, "capabilities": result, "unknown_is_blocking": True}


def _required_capabilities(contract):
    required = ["evidence.hash", "checkpoint.persist"]
    if contract.get("policy", {}).get("workspace_write", True):
        required.extend(["fs.read", "fs.write", "shell.exec"])
    if contract.get("policy", {}).get("ux_required"):
        required.extend(["browser.open", "browser.control"])
    if contract.get("policy", {}).get("external_actions"):
        required.append("external.authorized")
    return sorted(set(required))


def start(contract, capabilities, *, adapter_id="unknown-adapter", host_id="unknown-host"):
    contract = validate_contract(contract)
    report = probe(capabilities)
    required = _required_capabilities(contract)
    missing = [name for name in required if report["capabilities"].get(name) != "supported"]
    unknown = [name for name in missing if report["capabilities"].get(name) in (None, "unknown")]
    status = "BLOCKED" if missing else "READY"
    blockers = [{"kind": "capability", "code": "CAPABILITY_REQUIRED", "capability": name,
                 "known": name not in unknown} for name in missing]
    return {
        "protocol": PROTOCOL, "task_id": contract["id"], "revision": 1,
        "contract_hash": digest(contract), "candidate": contract["candidate"],
        "phase": "assess", "status": status, "owner": contract["owner"],
        "adapter_id": adapter_id, "host_id": host_id,
        "capabilities": report["capabilities"], "required_capabilities": required,
        "blockers": blockers, "events": [], "evidence": [], "findings": [],
        "open_findings": [], "checkpoints": [], "active_executor": None,
        "unknown_tool_calls": [], "idempotency": {}, "uncovered": [],
        "next_action": "RESOLVE_CAPABILITIES" if missing else "PLAN",
        "created_at": _now(), "updated_at": _now(),
    }


def _check_header(state, request):
    _require(request.get("protocol") == PROTOCOL, "unsupported protocol")
    for key in ("request_id", "op", "idempotency_key"):
        _require(isinstance(request.get(key), str) and request[key].strip(), "request missing " + key)
    _require(request.get("task_id") == state["task_id"], "task mismatch")
    _require(request.get("revision") == state["revision"], "STALE_REVISION")
    _require(request.get("contract_hash") == state["contract_hash"], "STALE_CONTRACT")
    _require(request.get("candidate") == state["candidate"], "STALE_CANDIDATE")


def _response(state, *, ok=True, next_action=None, error=None):
    return {"protocol": PROTOCOL, "ok": ok, "task_id": state["task_id"],
            "revision": state["revision"], "phase": state["phase"],
            "status": state["status"], "checkpoint": state["checkpoints"][-1] if state["checkpoints"] else None,
            "evidence": state["evidence"][-5:], "failure": error,
            "next_action": next_action or state["next_action"]}


def _event(state, kind, payload):
    state["events"].append({"seq": len(state["events"])+1, "kind": kind, "payload": payload, "at": _now()})
    state["updated_at"] = _now()


def apply(state, request):
    """Apply one idempotent host/model event and return (new_state, response)."""
    _check_header(state, request)
    key = request["idempotency_key"]
    if key in state["idempotency"]:
        return deepcopy(state), deepcopy(state["idempotency"][key])
    state = deepcopy(state)
    op = request["op"]
    payload = request.get("payload") or {}
    result = None
    if op == "start":
        _require(state["status"] == "READY", "task is not ready")
        state["status"], state["phase"], state["active_executor"] = "RUNNING", "work", payload.get("executor")
        state["next_action"] = "EXECUTE_NEXT_STEP"; _event(state, "STARTED", payload)
    elif op == "observe":
        if payload.get("checkpoint_due"):
            state["next_action"] = "DECIDE_CONTINUE_OR_PAUSE"; _event(state, "CHECKPOINT_REACHED", payload)
        else:
            state["next_action"] = "CONTINUE"; _event(state, "OBSERVED", payload)
    elif op == "tool_result":
        outcome = payload.get("status")
        _require(outcome in ("SUCCEEDED", "FAILED", "UNKNOWN"), "invalid tool result status")
        _require(payload.get("tool_call_id"), "tool_call_id required")
        _event(state, "TOOL_RESULT", payload)
        if outcome == "UNKNOWN":
            state["status"] = "UNKNOWN"; state["unknown_tool_calls"].append(payload["tool_call_id"])
            state["next_action"] = "READBACK_OR_RECONCILE"
        elif outcome == "FAILED":
            state["status"] = "BLOCKED"; state["next_action"] = "DIAGNOSE_AND_ADJUST"
        else:
            state["next_action"] = "RECORD_EVIDENCE"
    elif op == "evidence":
        evidence = evidence_ref(payload, task_id=state["task_id"], revision=state["revision"],
                                contract_hash=state["contract_hash"], candidate=state["candidate"],
                                producer=state["adapter_id"])
        state["evidence"].append(evidence); _event(state, "EVIDENCE_RECORDED", {"id": evidence["id"]})
        if evidence["verdict"] != "PASS":
            state["status"] = "BLOCKED"; state["next_action"] = "RESOLVE_EVIDENCE"
        else:
            state["next_action"] = "CONTINUE"
        if evidence["verdict"] == "PASS" and state["status"] in ("UNKNOWN", "BLOCKED"):
            state["status"] = "RUNNING"; state["next_action"] = "CONTINUE"
    elif op == "open_finding":
        finding_id = payload.get("id")
        _require(isinstance(finding_id, str) and finding_id, "finding id required")
        state["findings"].append(deepcopy(payload)); state["open_findings"].append(finding_id)
        state["status"], state["phase"], state["next_action"] = "BLOCKED", "review", "FIX_AND_RETEST"
        _event(state, "FINDING_OPEN", payload)
    elif op == "checkpoint":
        checkpoint = {"id": payload.get("id"), "seq": len(state["checkpoints"])+1,
                      "task_id": state["task_id"], "revision": state["revision"],
                      "contract_hash": state["contract_hash"], "candidate": state["candidate"],
                      "phase": state["phase"], "cursor": payload.get("cursor"),
                      "artifact_refs": payload.get("artifact_refs", []), "created_at": _now(),
                      "resume_token": payload.get("resume_token")}
        _require(checkpoint["id"] and checkpoint["resume_token"], "checkpoint id and resume_token required")
        state["checkpoints"].append(checkpoint); _event(state, "CHECKPOINT_SAVED", checkpoint)
    elif op == "pause":
        _require(state["status"] in ("RUNNING", "UNKNOWN", "BLOCKED"), "cannot pause current state")
        _require(payload.get("executor_stopped") is True, "PAUSE_REQUIRES_EXECUTOR_STOP_PROOF")
        _require(state["checkpoints"], "PAUSE_REQUIRES_CHECKPOINT")
        state["status"], state["next_action"] = "PAUSED", "RESUME_OR_HANDOFF"; state["active_executor"] = None
        _event(state, "PAUSED", payload)
    elif op == "resume":
        _require(state["status"] == "PAUSED", "task is not paused")
        _require(payload.get("resume_token") == state["checkpoints"][-1]["resume_token"], "STALE_CHECKPOINT")
        _require(payload.get("executor_verified") is True, "RESUME_REQUIRES_OWNER_VERIFICATION")
        state["status"], state["next_action"], state["active_executor"] = "RUNNING", "CONTINUE", payload.get("executor")
        _event(state, "RESUMED", payload)
    elif op == "phase":
        phase = payload.get("phase"); _require(phase in PHASES, "invalid phase")
        state["phase"] = phase; state["next_action"] = "RECORD_EVIDENCE" if phase in ("verify", "review") else "CONTINUE"
        _event(state, "PHASE_CHANGED", payload)
    elif op == "finish":
        blockers = []
        if state["status"] in ("UNKNOWN", "BLOCKED", "PAUSED"): blockers.append("STATUS_NOT_DELIVERABLE")
        if state["open_findings"]: blockers.append("OPEN_FINDINGS")
        if state["uncovered"]: blockers.append("UNCOVERED_ACCEPTANCE")
        if state["unknown_tool_calls"]: blockers.append("UNKNOWN_SIDE_EFFECT")
        if not state["evidence"] or any(e["verdict"] != "PASS" for e in state["evidence"]): blockers.append("NON_PASS_EVIDENCE")
        if state["active_executor"] and payload.get("executor_stopped") is not True:
            blockers.append("ACTIVE_EXECUTOR")
        if blockers:
            state["status"], state["next_action"] = "BLOCKED", "RESOLVE_DELIVERY_BLOCKERS"; state["blockers"] = [{"code": x} for x in blockers]
        else:
            state["active_executor"] = None
            state["status"], state["phase"], state["next_action"] = "COMPLETED", "deliver", "DONE"
        _event(state, "FINISH_ATTEMPT", {"blockers": blockers})
    elif op == "cancel":
        _require(payload.get("explicit") is True, "CANCEL_REQUIRES_EXPLICIT_REQUEST")
        state["status"], state["next_action"] = "CANCELLED", "STOP"; state["active_executor"] = None; _event(state, "CANCELLED", payload)
    else:
        raise ProtocolError("unsupported operation: " + str(op))
    result = _response(state, next_action=state["next_action"])
    state["idempotency"][key] = result
    return state, result
