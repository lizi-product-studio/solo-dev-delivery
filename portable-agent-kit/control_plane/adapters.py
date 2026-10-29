"""Bound receipts for semantic, UI, business and host/platform acceptance."""
from __future__ import annotations

from copy import deepcopy
from .contracts import canonical_hash


class ReceiptError(ValueError):
    pass


KINDS = ("semantic", "ui", "business", "platform")


def verify_receipt(receipt, state, *, reviewer_id, expected_kind):
    if expected_kind not in KINDS:
        raise ReceiptError("unsupported receipt kind")
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt must be an object")
    if receipt.get("format") != "acceptance-receipt/1":
        raise ReceiptError("unsupported receipt format")
    if receipt.get("kind") != expected_kind:
        raise ReceiptError("receipt kind mismatch")
    if receipt.get("status") != "PASS":
        raise ReceiptError("receipt is not PASS")
    if receipt.get("reviewer_id") != reviewer_id or reviewer_id == state.get("owner"):
        raise ReceiptError("reviewer is not independent")
    for key in ("task_id", "contract_hash", "candidate", "revision"):
        if receipt.get(key) != state.get(key):
            raise ReceiptError("receipt binding mismatch: " + key)
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReceiptError("receipt checks are required")
    if any(not isinstance(row, dict) or row.get("status") != "PASS" or not row.get("check_id")
           for row in checks):
        raise ReceiptError("every receipt check must be PASS")
    clean = deepcopy(receipt)
    clean["receipt_sha256"] = canonical_hash(receipt)
    return clean


def make_receipt(state, kind, reviewer_id, checks, *, observations=None):
    if kind not in KINDS:
        raise ReceiptError("unsupported receipt kind")
    if reviewer_id == state.get("owner"):
        raise ReceiptError("reviewer is not independent")
    receipt = {"format": "acceptance-receipt/1", "kind": kind, "status": "PASS",
               "task_id": state["task_id"], "revision": state["revision"],
               "contract_hash": state["contract_hash"], "candidate": state["candidate"],
               "reviewer_id": reviewer_id, "checks": deepcopy(checks),
               "observations": deepcopy(observations or {})}
    return verify_receipt(receipt, state, reviewer_id=reviewer_id, expected_kind=kind)
