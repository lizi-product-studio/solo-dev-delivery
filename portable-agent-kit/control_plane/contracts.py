"""Structured task contracts used by the runtime and independent verifier."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy


CONTRACT_FORMAT = "delivery-contract/1"
RISK_LEVELS = ("low", "medium", "high", "critical")
ENVIRONMENTS = ("isolated", "local", "staging", "production", "browser", "native")


class ContractError(ValueError):
    """Raised when a task contract cannot be made executable and auditable."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(name + " must be a non-empty string")
    return value.strip()


def _list(value, name, *, allow_empty=False):
    if not isinstance(value, list):
        raise ContractError(name + " must be a list")
    if not allow_empty and not value:
        raise ContractError(name + " must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(name + " must contain non-empty strings")
    return [item.strip() for item in value]


def _bool(value, name, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(name + " must be boolean")
    return value


def _positive_int(value, name, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(name + " must be a positive integer")
    return value


def canonical_hash(value):
    """Return a deterministic SHA-256 for a JSON-compatible object."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_acceptance(rows):
    if not isinstance(rows, list) or not rows:
        raise ContractError("acceptance must be a non-empty list")
    seen = set()
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContractError("acceptance[%d] must be an object" % index)
        check_id = _text(row.get("id"), "acceptance[%d].id" % index)
        if check_id in seen:
            raise ContractError("duplicate acceptance id: " + check_id)
        seen.add(check_id)
        expected = _text(row.get("expected"), "acceptance[%d].expected" % index)
        environment = _text(row.get("environment", "isolated"),
                            "acceptance[%d].environment" % index)
        if environment not in ENVIRONMENTS:
            raise ContractError("unsupported acceptance environment: " + environment)
        clean = deepcopy(row)
        clean.update({"id": check_id, "expected": expected,
                      "environment": environment})
        assertion = clean.get("assertion")
        if assertion is not None:
            if not isinstance(assertion, dict):
                raise ContractError("acceptance[%d].assertion must be an object" % index)
            assertion_type = _text(assertion.get("type"),
                                   "acceptance[%d].assertion.type" % index)
            allowed = {"file_exists", "file_sha256", "text_contains",
                       "json_pointer_equals", "receipt_pass"}
            if assertion_type not in allowed:
                raise ContractError("unsupported assertion type: " + assertion_type)
            clean["assertion"] = deepcopy(assertion)
            clean["assertion"]["type"] = assertion_type
        result.append(clean)
    return result


def normalize_contract(raw):
    """Normalize the public contract envelope to the state-machine contract.

    The existing AAP state machine remains the transition authority.  This
    function adds explicit risk, budget, stop and recovery fields and accepts
    ``task_id``/``id`` and ``authorization``/``authorization_ref`` aliases so
    the same contract can be used by a Skill, CLI, or host adapter.
    """
    if not isinstance(raw, dict):
        raise ContractError("contract must be an object")
    task_id = _text(raw.get("task_id", raw.get("id")), "task_id")
    goal = _text(raw.get("goal"), "goal")
    owner = _text(raw.get("owner"), "owner")
    candidate = _text(raw.get("candidate"), "candidate")
    scope = _list(raw.get("scope"), "scope")
    excluded = _list(raw.get("excluded"), "excluded")
    authorization = raw.get("authorization", {})
    if authorization is None:
        authorization = {}
    if not isinstance(authorization, dict):
        raise ContractError("authorization must be an object")
    authorization_ref = raw.get("authorization_ref", authorization.get("ref"))
    authorization_ref = _text(authorization_ref or "local", "authorization_ref")

    policy = raw.get("policy", {})
    if not isinstance(policy, dict):
        raise ContractError("policy must be an object")
    policy = deepcopy(policy)
    policy["external_actions"] = _bool(
        authorization.get("external_actions", policy.get("external_actions")),
        "policy.external_actions", False)
    policy["workspace_write"] = _bool(policy.get("workspace_write"),
                                       "policy.workspace_write", True)
    policy["ux_required"] = _bool(policy.get("ux_required"),
                                   "policy.ux_required", False)

    risk = raw.get("risk", {})
    if risk is None:
        risk = {}
    if not isinstance(risk, dict):
        raise ContractError("risk must be an object")
    risk = deepcopy(risk)
    risk["level"] = _text(risk.get("level", "medium"), "risk.level").lower()
    if risk["level"] not in RISK_LEVELS:
        raise ContractError("risk.level must be low, medium, high or critical")
    risk["reversible"] = _bool(risk.get("reversible"), "risk.reversible", True)
    risk["impacts"] = _list(risk.get("impacts", ["local-deliverable"]),
                             "risk.impacts")

    budget = raw.get("budget", {})
    if budget is None:
        budget = {}
    if not isinstance(budget, dict):
        raise ContractError("budget must be an object")
    budget = deepcopy(budget)
    budget["max_steps"] = _positive_int(budget.get("max_steps"),
                                         "budget.max_steps", 20)
    budget["max_retries"] = _positive_int(budget.get("max_retries"),
                                           "budget.max_retries", 2)
    if "max_seconds" in budget:
        budget["max_seconds"] = _positive_int(budget["max_seconds"],
                                               "budget.max_seconds", 3600)
    else:
        budget["max_seconds"] = 3600

    required_capabilities = _list(raw.get("required_capabilities", []),
                                  "required_capabilities", allow_empty=True)
    stop_conditions = _list(raw.get("stop_conditions", [
        "required capability is unknown or unsupported",
        "external result cannot be reconciled",
        "budget or authorization boundary is reached",
    ]), "stop_conditions")
    recovery = raw.get("recovery", {})
    if recovery is None:
        recovery = {}
    if not isinstance(recovery, dict):
        raise ContractError("recovery must be an object")
    recovery = deepcopy(recovery)
    recovery.setdefault("unknown_result", "reconcile_before_retry")
    recovery.setdefault("repeated_failure", "diagnose_then_split_or_handoff")

    result = deepcopy(raw)
    result.update({
        "format": CONTRACT_FORMAT,
        "id": task_id,
        "task_id": task_id,
        "goal": goal,
        "owner": owner,
        "candidate": candidate,
        "authorization_ref": authorization_ref,
        "authorization": authorization,
        "scope": scope,
        "excluded": excluded,
        "policy": policy,
        "risk": risk,
        "budget": budget,
        "required_capabilities": required_capabilities,
        "stop_conditions": stop_conditions,
        "recovery": recovery,
        "acceptance": _normalize_acceptance(raw.get("acceptance")),
    })
    # The state machine computes the binding hash from this normalized object.
    # Do not embed a hash of the object inside itself: that would create two
    # different meanings for ``contract_hash`` and make stale detection harder.
    result.pop("contract_hash", None)
    return result


def public_contract(raw):
    """Return a normalized copy without the derived hash field."""
    value = normalize_contract(raw)
    return value
