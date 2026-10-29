"""Independent, evidence-first acceptance checks.

The evaluator authenticates local evidence and deterministic assertions.  It
never accepts a model-supplied ``verified`` flag.  Semantic, UX, production and
identity checks must be supplied by a real host adapter as a separate receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
import math
from copy import deepcopy
from pathlib import Path

from .contracts import canonical_hash, normalize_contract


class EvaluationError(ValueError):
    """Raised for malformed or unauthenticated evidence."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(name + " must be a non-empty string")
    return value.strip()


def _safe_path(root, uri):
    uri = _text(uri, "evidence.uri")
    path = Path(uri)
    if path.is_absolute() or ".." in path.parts:
        raise EvaluationError("evidence URI must stay inside evidence_root")
    root = Path(root).resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise EvaluationError("evidence URI escapes evidence_root") from None
    return target


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_pointer(value, pointer):
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise EvaluationError("json pointer must start with '/'")
    current = value
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                raise EvaluationError("json pointer target missing") from None
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise EvaluationError("json pointer target missing")
    return current


def _assertion(path, assertion):
    if not isinstance(assertion, dict):
        raise EvaluationError("missing deterministic assertion")
    assertion_type = assertion.get("type")
    if assertion_type == "file_exists":
        if not path.is_file():
            raise EvaluationError("asserted file does not exist")
        return True
    if assertion_type == "file_sha256":
        expected = _text(assertion.get("sha256"), "assertion.sha256").lower()
        if _sha256(path) != expected:
            raise EvaluationError("assertion file hash mismatch")
        return True
    if assertion_type == "text_contains":
        needle = _text(assertion.get("text"), "assertion.text")
        text = path.read_text(encoding=assertion.get("encoding", "utf-8"))
        if needle not in text:
            raise EvaluationError("assertion text was not found")
        return True
    if assertion_type == "json_pointer_equals":
        pointer = _text(assertion.get("pointer"), "assertion.pointer")
        expected = assertion.get("value")
        value = json.loads(path.read_text(encoding="utf-8"))
        if 'value' not in assertion or not _same_json(_json_pointer(value, pointer),expected):
            raise EvaluationError("assertion JSON value mismatch")
        return True
    if assertion_type == "receipt_pass":
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("status") != "PASS":
            raise EvaluationError("bound receipt is not PASS")
        return True
    raise EvaluationError("unsupported deterministic assertion")


def _same_json(actual,expected):
    if isinstance(actual,bool) or isinstance(expected,bool):
        return type(actual) is type(expected) and actual==expected
    if isinstance(actual,(int,float)) and isinstance(expected,(int,float)):
        return ((not isinstance(actual,float) or math.isfinite(actual))
                and (not isinstance(expected,float) or math.isfinite(expected)) and actual==expected)
    if type(actual) is not type(expected):return False
    if isinstance(actual,list):
        return len(actual)==len(expected) and all(_same_json(a,b) for a,b in zip(actual,expected))
    if isinstance(actual,dict):
        return set(actual)==set(expected) and all(_same_json(actual[k],expected[k]) for k in actual)
    return actual==expected


def verify_evidence(ref, acceptance, evidence_root, *, reviewer_id, owner):
    """Verify one evidence reference and return a trusted observation.

    The caller must keep the returned receipt.  The reference's own verdict is
    necessary but not sufficient; the file hash and the contract assertion are
    checked independently here.
    """
    if not isinstance(ref, dict):
        raise EvaluationError("evidence must be an object")
    reviewer_id = _text(reviewer_id, "reviewer_id")
    owner = _text(owner, "owner")
    if reviewer_id == owner:
        raise EvaluationError("independent reviewer must differ from task owner")
    for key in ("id", "check_id", "uri", "sha256", "verdict"):
        _text(ref.get(key), "evidence." + key)
    if ref["verdict"] != "PASS":
        raise EvaluationError("only PASS evidence can be accepted")
    checks = {row["id"]: row for row in acceptance}
    check = checks.get(ref["check_id"])
    if check is None:
        raise EvaluationError("evidence check_id is not in the contract")
    if ref.get('environment')!=check['environment']:
        raise EvaluationError('evidence environment mismatch')
    path = _safe_path(evidence_root, ref["uri"])
    if not path.is_file():
        raise EvaluationError("evidence file does not exist")
    actual_hash = _sha256(path)
    if actual_hash.lower() != ref["sha256"].lower():
        raise EvaluationError("evidence hash mismatch")
    assertion = check.get("assertion")
    if assertion is None:
        raise EvaluationError("acceptance has no independent deterministic assertion")
    if assertion.get('path') and _safe_path(evidence_root,assertion['path'])!=path:
        raise EvaluationError('evidence artifact path differs from contract')
    _assertion(path, assertion)
    return {
        "status": "PASS",
        "evidence_id": ref["id"],
        "check_id": ref["check_id"],
        "uri": ref["uri"],
        "sha256": actual_hash,
        "reviewer_id": reviewer_id,
        "independent": True,
    }


def make_verifier(contract, evidence_root, *, reviewer_id):
    """Create the trusted callback expected by ``core.state_machine``."""
    acceptance = deepcopy(contract.get("acceptance", []))
    owner = contract.get("owner", "")

    def verifier(ref):
        try:
            verify_evidence(ref, acceptance, evidence_root,
                            reviewer_id=reviewer_id, owner=owner)
            return True
        except (EvaluationError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return False

    return verifier


def verify_task(state, contract, evidence_root, *, reviewer_id):
    """Independently verify every current acceptance item in a state snapshot."""
    if not isinstance(state, dict) or not isinstance(contract, dict):
        raise EvaluationError("state and contract must be objects")
    owner = _text(contract.get("owner"), "contract.owner")
    reviewer_id = _text(reviewer_id, "reviewer_id")
    if reviewer_id == owner:
        raise EvaluationError("independent reviewer must differ from task owner")
    normalized = normalize_contract(contract)
    expected_hash = canonical_hash(normalized)
    if state.get("contract_hash") != expected_hash:
        raise EvaluationError("state contract binding mismatch")
    if state.get("status") in {"BLOCKED", "UNKNOWN", "PAUSED", "NEEDS_INPUT", "FAILED", "CANCELLED"}:
        raise EvaluationError("task state is not independently deliverable: " + str(state.get("status")))
    if state.get("blockers"):
        raise EvaluationError("task has unresolved delivery blockers")
    current = {}
    for ref in state.get("evidence", []):
        check_id = ref.get("check_id")
        if check_id:
            current[check_id] = ref
    checks = []
    for item in contract.get("acceptance", []):
        ref = current.get(item.get("id"))
        if ref is None:
            checks.append({"check_id": item.get("id"), "status": "INCOMPLETE",
                           "reason": "missing current evidence"})
            continue
        try:
            if any(ref.get(k)!=state.get(k) for k in ('task_id','revision','contract_hash','candidate')):
                raise EvaluationError('evidence state binding mismatch')
            checks.append(verify_evidence(ref, contract["acceptance"], evidence_root,
                                          reviewer_id=reviewer_id, owner=owner))
        except (EvaluationError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            checks.append({"check_id": item.get("id"), "status": "FAIL",
                           "reason": str(exc)})
    status = "PASS" if checks and all(item["status"] == "PASS" for item in checks) else "INCOMPLETE"
    if any(item["status"] == "FAIL" for item in checks):
        status = "FAIL"
    return {
        "format": "independent-review/1",
        "status": status,
        "task_id": state.get("task_id"),
        "revision": state.get("revision"),
        "contract_hash": state.get("contract_hash"),
        "candidate": state.get("candidate"),
        "reviewer_id": reviewer_id,
        "owner": owner,
        "independent": True,
        "checks": checks,
        "evidence_root": str(Path(evidence_root).resolve()),
        "limits": "Deterministic evidence only; semantic, UX, identity and production claims need a real host receipt.",
    }
