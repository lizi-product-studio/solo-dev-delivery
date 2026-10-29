"""Provider-neutral delivery gates, with evidence verified by a trusted host.

The core has no filesystem, model, network or vendor dependencies. Contracts,
state storage and the verifier belong to the host; this is not an identity or
filesystem sandbox. Model-supplied PASS/verified fields never authenticate proof.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from .capabilities import normalize

PROTOCOL = "aap/0.1"
STATE_VERSION = 2
PHASES = ("assess", "work", "verify", "review", "deliver")
STATUSES = ("READY", "RUNNING", "PAUSED", "BLOCKED", "UNKNOWN", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
_VERIFICATION_FIELDS = ("verified", "host_verified", "verification", "verification_error")


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
    """Validate reference shape/binding, without trusting its verdict or flags."""
    _require(isinstance(value, dict), "evidence must be an object")
    for key in ("id", "kind", "uri", "sha256", "claim", "verdict"):
        _require(isinstance(value.get(key), str) and value[key].strip(), "evidence missing " + key)
    _require(value["verdict"] in ("PASS", "FAIL", "UNKNOWN"), "invalid evidence verdict")
    _require(re.fullmatch(r"[0-9a-fA-F]{64}", value["sha256"]) is not None,
             "evidence sha256 must be a hex digest")
    _require(value.get("task_id") == task_id, "evidence task mismatch")
    _require(value.get("revision") == revision, "evidence revision mismatch")
    _require(value.get("contract_hash") == contract_hash, "evidence contract mismatch")
    _require(value.get("candidate") == candidate, "evidence candidate mismatch")
    if "check_id" in value:
        _require(isinstance(value["check_id"], str) and value["check_id"].strip(), "invalid evidence check_id")
    value = deepcopy(value)
    for key in _VERIFICATION_FIELDS + ("recorded_seq", "supersedes", "superseded_by"):
        value.pop(key, None)
    value["producer"] = producer
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
    for key in ("external_actions", "workspace_write", "ux_required", "shell_required"):
        if key in policy:
            _require(isinstance(policy[key], bool), "policy." + key + " must be boolean")
    required = contract.get("required_capabilities", [])
    _require(isinstance(required, list) and all(isinstance(x, str) and x.strip() for x in required),
             "required_capabilities must be a list of non-empty strings")
    return deepcopy(contract)


def probe(capabilities):
    """Return a three-state report; unknown never grants a capability."""
    _require(isinstance(capabilities, dict), "capabilities must be an object")
    try:
        result = normalize(capabilities)
    except ValueError as error:
        raise ProtocolError(str(error)) from None
    return {"protocol": PROTOCOL, "capabilities": result, "unknown_is_blocking": True}


def _required_capabilities(contract):
    required = ["evidence.hash", "checkpoint.persist", *contract.get("required_capabilities", [])]
    if contract.get("policy", {}).get("workspace_write", True):
        required.extend(["fs.read", "fs.write"])
        if contract.get('policy',{}).get('shell_required',True):
            required.append('shell.exec')
    if contract.get("policy", {}).get("ux_required"):
        required.extend(["browser.open", "browser.control"])
    if contract.get("policy", {}).get("external_actions"):
        required.append("external.authorized")
    return sorted(set(required))


def _blocker(code, kind, **fields):
    return {"source": "core", "kind": kind, "code": code, **fields}


def start(contract, capabilities, *, adapter_id="unknown-adapter", host_id="unknown-host"):
    contract = validate_contract(contract)
    report = probe(capabilities)
    required = _required_capabilities(contract)
    missing = [name for name in required if report["capabilities"].get(name) != "supported"]
    blockers = [_blocker("CAPABILITY_REQUIRED", "capability", capability=name,
                         known=report["capabilities"].get(name) == "unsupported") for name in missing]
    return {
        "protocol": PROTOCOL, "state_version": STATE_VERSION, "task_id": contract["id"], "revision": 1,
        "contract_hash": digest(contract), "candidate": contract["candidate"],
        "contract": contract, "acceptance": deepcopy(contract["acceptance"]),
        "policy": deepcopy(contract.get("policy", {})),
        "phase": "assess", "status": "BLOCKED" if missing else "READY", "owner": contract["owner"],
        "adapter_id": adapter_id, "host_id": host_id,
        "capabilities": report["capabilities"], "required_capabilities": required,
        "blockers": blockers, "events": [], "evidence": [], "current_evidence": {}, "findings": [],
        "open_findings": [], "checkpoints": [], "active_executor": None,
        "unknown_tool_calls": [], "unknown_tool_details": {}, "failed_tool_calls": [],
        "idempotency": {}, "uncovered": [row["id"] for row in contract["acceptance"]],
        "started": False, "finish_attempted": False,
        "next_action": "RESOLVE_CAPABILITIES" if missing else "PLAN",
        "created_at": _now(), "updated_at": _now(),
    }


def _check_header(state, request):
    _require(isinstance(request, dict), "request must be an object")
    _require(request.get("protocol") == PROTOCOL, "unsupported protocol")
    for key in ("request_id", "op", "idempotency_key"):
        _require(isinstance(request.get(key), str) and request[key].strip(), "request missing " + key)
    _require(request.get("task_id") == state["task_id"], "task mismatch")
    _require(request.get("revision") == state["revision"], "STALE_REVISION")
    _require(request.get("contract_hash") == state["contract_hash"], "STALE_CONTRACT")
    _require(request.get("candidate") == state["candidate"], "STALE_CANDIDATE")
    _require(isinstance(request.get("payload", {}), dict), "payload must be an object")


def _response(state, *, ok=True, next_action=None, error=None):
    return {"protocol": PROTOCOL, "ok": ok, "task_id": state["task_id"],
            "revision": state["revision"], "phase": state["phase"],
            "status": state["status"], "checkpoint": state["checkpoints"][-1] if state["checkpoints"] else None,
            "evidence": deepcopy(state["evidence"][-5:]), "failure": error,
            "next_action": next_action or state["next_action"]}


def _event(state, kind, payload):
    state["events"].append({"seq": len(state["events"])+1, "kind": kind, "payload": deepcopy(payload), "at": _now()})
    state["updated_at"] = _now()


def _acceptance(state):
    return {row["id"]: row for row in state["acceptance"]}


def _current(state):
    """The latest reference replaces only its own check, never other blockers."""
    checks = _acceptance(state)
    selected = {}
    for ref in state["evidence"]:
        if ref.get("check_id") in checks:
            selected[ref["check_id"]] = ref
    state["current_evidence"] = {check: ref["id"] for check, ref in selected.items()}
    return selected


def _verify(state, ref, verify_evidence):
    authenticated = False
    try:
        clean = evidence_ref(ref, task_id=state["task_id"], revision=state["revision"],
                             contract_hash=state["contract_hash"], candidate=state["candidate"],
                             producer=state["adapter_id"])
        check = _acceptance(state).get(clean.get("check_id"))
        if check and clean.get("environment") == check["environment"] and callable(verify_evidence):
            # Give the host a copy: neither caller flags nor callback mutations
            # become proof or mutate the persisted reference.
            authenticated = verify_evidence(deepcopy(clean)) is True
    except Exception:
        authenticated = False
    for key in _VERIFICATION_FIELDS:
        ref.pop(key, None)
    ref["host_verified"] = authenticated
    ref["verification"] = "verified" if authenticated else "unverified"
    return authenticated


def _refresh(state):
    selected = _current(state)
    state["uncovered"] = [check for check in _acceptance(state)
                          if check not in selected or selected[check].get("host_verified") is not True
                          or selected[check]["verdict"] != "PASS"]
    # Host-added blockers survive evidence updates. Only core-derived blockers
    # are recomputed from their own source of truth.
    blockers = [deepcopy(b) for b in state["blockers"] if b.get("source") != "core"]
    for name in state["required_capabilities"]:
        capability = state["capabilities"].get(name)
        if capability != "supported":
            blockers.append(_blocker("CAPABILITY_REQUIRED", "capability", capability=name,
                                     known=capability == "unsupported"))
    for call_id in state["unknown_tool_calls"]:
        blockers.append(_blocker("UNKNOWN_SIDE_EFFECT", "tool", tool_call_id=call_id))
    for call_id in state["failed_tool_calls"]:
        blockers.append(_blocker("TOOL_FAILED", "tool", tool_call_id=call_id))
    for finding_id in state["open_findings"]:
        blockers.append(_blocker("OPEN_FINDINGS", "finding", finding_id=finding_id))
    for check, ref in selected.items():
        if ref["verdict"] != "PASS":
            blockers.append(_blocker("NON_PASS_EVIDENCE", "evidence", check_id=check, evidence_id=ref["id"]))
        elif ref.get("host_verified") is not True:
            blockers.append(_blocker("UNVERIFIED_EVIDENCE", "evidence", check_id=check, evidence_id=ref["id"]))
    if state["finish_attempted"] and state["uncovered"]:
        blockers.append(_blocker("UNCOVERED_ACCEPTANCE", "coverage", check_ids=state["uncovered"][:]))
        if not selected:
            blockers.append(_blocker("NON_PASS_EVIDENCE", "evidence"))
    if state["finish_attempted"] and state["active_executor"]:
        blockers.append(_blocker("ACTIVE_EXECUTOR", "executor"))
    state["blockers"] = blockers
    if state["status"] in TERMINAL or state["status"] in ("PAUSED", "NEEDS_INPUT"):
        return
    if state["unknown_tool_calls"]:
        state["status"], state["next_action"] = "UNKNOWN", "READBACK_OR_RECONCILE"
    elif blockers:
        state["status"], state["next_action"] = "BLOCKED", "RESOLVE_DELIVERY_BLOCKERS"
    elif state["started"]:
        state["status"], state["next_action"] = "RUNNING", "CONTINUE"
    else:
        state["status"], state["next_action"] = "READY", "PLAN"


def apply(state, request, *, verify_evidence=None):
    """Apply an event and return (new_state, response).

    The optional trusted host verifier receives an evidence copy and must return
    exactly True after checking actual proof. Missing/throwing verifiers fail
    closed. Every finish revalidates every current check's evidence.
    """
    _require(isinstance(state, dict) and state.get("state_version") == STATE_VERSION, "STATE_UPGRADE_REQUIRED")
    _check_header(state, request)
    key = request["idempotency_key"]
    fingerprint = digest(request)
    if key in state["idempotency"]:
        saved = state["idempotency"][key]
        _require(saved.get("request_hash") == fingerprint, "IDEMPOTENCY_KEY_CONFLICT")
        return deepcopy(state), deepcopy(saved["response"])
    _require(state["status"] not in TERMINAL, "TERMINAL_STATE_LOCKED")
    state = deepcopy(state)
    op = request["op"]
    payload = request.get("payload", {})
    if op == "start":
        _refresh(state)
        _require(state["status"] == "READY" and not state["started"], "task is not ready")
        state["started"] = True
        state["status"], state["phase"], state["active_executor"] = "RUNNING", "work", payload.get("executor")
        state["next_action"] = "EXECUTE_NEXT_STEP"
        _event(state, "STARTED", payload)
    elif op == "observe":
        # Thresholds request a decision; they never stop or replace an executor.
        state["next_action"] = "DECIDE_CONTINUE_OR_PAUSE" if payload.get("checkpoint_due") else "CONTINUE"
        _event(state, "CHECKPOINT_REACHED" if payload.get("checkpoint_due") else "OBSERVED", payload)
    elif op == "tool_result":
        outcome, call_id = payload.get("status"), payload.get("tool_call_id")
        _require(outcome in ("SUCCEEDED", "FAILED", "UNKNOWN"), "invalid tool result status")
        _require(isinstance(call_id, str) and call_id.strip(), "tool_call_id required")
        _event(state, "TOOL_RESULT", payload)
        if outcome == "UNKNOWN":
            if call_id not in state["unknown_tool_calls"]:
                state["unknown_tool_calls"].append(call_id)
            state["unknown_tool_details"][call_id] = len(state["events"])
        elif outcome == "FAILED":
            if call_id not in state["failed_tool_calls"]:
                state["failed_tool_calls"].append(call_id)
        elif call_id not in state["unknown_tool_calls"]:
            state["failed_tool_calls"] = [x for x in state["failed_tool_calls"] if x != call_id]
        _refresh(state)
        if not state["blockers"] and outcome == "SUCCEEDED":
            state["next_action"] = "RECORD_EVIDENCE"
    elif op == "evidence":
        ref = evidence_ref(payload, task_id=state["task_id"], revision=state["revision"],
                           contract_hash=state["contract_hash"], candidate=state["candidate"],
                           producer=state["adapter_id"])
        _require(not any(e["id"] == ref["id"] for e in state["evidence"]), "duplicate evidence id")
        if "check_id" in ref:
            _require(ref["check_id"] in _acceptance(state), "unknown evidence check_id")
        previous = _current(state).get(ref.get("check_id"))
        if previous:
            ref["supersedes"] = previous["id"]
            previous["superseded_by"] = ref["id"]
        ref["recorded_seq"] = len(state["events"]) + 1
        _verify(state, ref, verify_evidence)
        state["evidence"].append(ref)
        _event(state, "EVIDENCE_RECORDED", {"id": ref["id"], "check_id": ref.get("check_id")})
        _refresh(state)
    elif op == "reconcile":
        call_id, evidence_id, outcome = payload.get("tool_call_id"), payload.get("evidence_id"), payload.get("observed_outcome")
        _require(call_id in state["unknown_tool_calls"], "tool call is not unknown")
        _require(outcome in ("SUCCEEDED", "FAILED"), "invalid observed_outcome")
        ref = next((e for e in _current(state).values() if e["id"] == evidence_id), None)
        _require(ref is not None and ref["kind"] == "readback", "RECONCILE_REQUIRES_READBACK_EVIDENCE")
        _require(ref.get("tool_call_id") == call_id and ref.get("observed_outcome") == outcome,
                 "READBACK_OUTCOME_MISMATCH")
        _require(ref.get("recorded_seq", 0) > state["unknown_tool_details"].get(call_id, 0), "READBACK_MUST_FOLLOW_UNKNOWN")
        _require(_verify(state, ref, verify_evidence) and ref["verdict"] == "PASS", "READBACK_NOT_VERIFIED")
        state["unknown_tool_calls"].remove(call_id)
        state["unknown_tool_details"].pop(call_id, None)
        if outcome == "FAILED" and call_id not in state["failed_tool_calls"]:
            state["failed_tool_calls"].append(call_id)
        elif outcome == "SUCCEEDED":
            state["failed_tool_calls"] = [x for x in state["failed_tool_calls"] if x != call_id]
        _event(state, "RECONCILED", payload)
        _refresh(state)
    elif op == "open_finding":
        finding_id = payload.get("id")
        _require(isinstance(finding_id, str) and finding_id.strip(), "finding id required")
        _require(not any(f["id"] == finding_id for f in state["findings"]), "duplicate finding id")
        checks = payload.get("check_ids", list(_acceptance(state)))
        _require(isinstance(checks, list) and checks and all(isinstance(x, str) and x in _acceptance(state) for x in checks),
                 "finding check_ids must name acceptance items")
        finding = deepcopy(payload)
        finding["check_ids"] = sorted(set(checks))
        finding["opened_seq"], finding["status"] = len(state["events"]) + 1, "OPEN"
        state["findings"].append(finding)
        state["open_findings"].append(finding_id)
        state["phase"] = "review"
        _event(state, "FINDING_OPEN", finding)
        _refresh(state)
        if state["status"] == "BLOCKED":
            state["next_action"] = "FIX_AND_RETEST"
    elif op == "resolve_finding":
        finding_id, ids = payload.get("id"), payload.get("evidence_ids")
        _require(finding_id in state["open_findings"], "finding is not open")
        _require(isinstance(ids, list) and ids and all(isinstance(x, str) for x in ids), "evidence_ids required")
        finding = next(f for f in state["findings"] if f["id"] == finding_id)
        selected = {e["id"]: e for e in _current(state).values()}
        covered = set()
        for evidence_id in ids:
            ref = selected.get(evidence_id)
            _require(ref is not None, "FINDING_REQUIRES_CURRENT_EVIDENCE")
            _require(ref.get("recorded_seq", 0) > finding["opened_seq"], "FINDING_REQUIRES_RETEST")
            _require(_verify(state, ref, verify_evidence) and ref["verdict"] == "PASS", "FINDING_EVIDENCE_NOT_VERIFIED")
            covered.add(ref["check_id"])
        _require(set(finding["check_ids"]) <= covered, "FINDING_CHECKS_UNCOVERED")
        finding["status"], finding["resolved_by"] = "RESOLVED", list(dict.fromkeys(ids))
        state["open_findings"].remove(finding_id)
        _event(state, "FINDING_RESOLVED", payload)
        _refresh(state)
    elif op == "checkpoint":
        checkpoint = {"id": payload.get("id"), "seq": len(state["checkpoints"])+1,
                      "task_id": state["task_id"], "revision": state["revision"],
                      "contract_hash": state["contract_hash"], "candidate": state["candidate"],
                      "phase": state["phase"], "cursor": payload.get("cursor"),
                      "artifact_refs": payload.get("artifact_refs", []), "created_at": _now(),
                      "resume_token": payload.get("resume_token")}
        _require(checkpoint["id"] and checkpoint["resume_token"], "checkpoint id and resume_token required")
        state["checkpoints"].append(checkpoint)
        _event(state, "CHECKPOINT_SAVED", checkpoint)
    elif op == "pause":
        _require(state["status"] in ("RUNNING", "UNKNOWN", "BLOCKED"), "cannot pause current state")
        _require(payload.get("executor_stopped") is True, "PAUSE_REQUIRES_EXECUTOR_STOP_PROOF")
        _require(state["checkpoints"], "PAUSE_REQUIRES_CHECKPOINT")
        state["status"], state["next_action"], state["active_executor"] = "PAUSED", "RESUME_OR_HANDOFF", None
        _event(state, "PAUSED", payload)
    elif op == "resume":
        _require(state["status"] == "PAUSED", "task is not paused")
        _require(payload.get("resume_token") == state["checkpoints"][-1]["resume_token"], "STALE_CHECKPOINT")
        _require(payload.get("executor_verified") is True, "RESUME_REQUIRES_OWNER_VERIFICATION")
        state["status"], state["active_executor"] = "RUNNING", payload.get("executor")
        _event(state, "RESUMED", payload)
        _refresh(state)
    elif op == "phase":
        phase = payload.get("phase")
        _require(phase in PHASES, "invalid phase")
        state["phase"] = phase
        state["next_action"] = "RECORD_EVIDENCE" if phase in ("verify", "review") else "CONTINUE"
        _event(state, "PHASE_CHANGED", payload)
    elif op == "finish":
        _require(state["started"], "TASK_NOT_STARTED")
        prior_status = state["status"]
        state["finish_attempted"] = True
        if payload.get("executor_stopped") is True:
            state["active_executor"] = None
        for ref in _current(state).values():
            _verify(state, ref, verify_evidence)
        _refresh(state)
        blockers = deepcopy(state["blockers"])
        if prior_status in ("UNKNOWN", "PAUSED", "NEEDS_INPUT"):
            state["status"] = prior_status
            blockers.append(_blocker("STATUS_NOT_DELIVERABLE", "status", status=prior_status))
        state["blockers"] = blockers
        if blockers:
            if state["status"] not in ("UNKNOWN", "PAUSED", "NEEDS_INPUT"):
                state["status"], state["next_action"] = "BLOCKED", "RESOLVE_DELIVERY_BLOCKERS"
        else:
            state["active_executor"] = None
            state["status"], state["phase"], state["next_action"] = "COMPLETED", "deliver", "DONE"
        _event(state, "FINISH_ATTEMPT", {"blockers": [b["code"] for b in blockers]})
    elif op == "cancel":
        _require(payload.get("explicit") is True, "CANCEL_REQUIRES_EXPLICIT_REQUEST")
        if state["active_executor"]:
            _require(payload.get("executor_stopped") is True, "CANCEL_REQUIRES_EXECUTOR_STOP_PROOF")
        state["status"], state["next_action"], state["active_executor"] = "CANCELLED", "STOP", None
        _event(state, "CANCELLED", payload)
    else:
        raise ProtocolError("unsupported operation: " + str(op))
    result = _response(state)
    state["idempotency"][key] = {"request_hash": fingerprint, "response": deepcopy(result)}
    return state, result
