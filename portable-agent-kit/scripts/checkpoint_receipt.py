#!/usr/bin/env python3
"""Create and validate a portable, host-owned checkpoint receipt.

The receipt is a small continuation record, not a scheduler or a proof that a
task completed.  It binds a cursor and resume token to the task contract and
candidate, so a host can detect stale handoffs before resuming.  Artifact
hashes are recorded as references supplied by the host; this module does not
read files, execute tools, wake processes, or authenticate those artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "checkpoint-receipt/1"
PHASES = ("assess", "work", "verify", "review", "deliver")
STATUSES = ("READY", "RUNNING", "PAUSED", "BLOCKED", "UNKNOWN", "NEEDS_INPUT")
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_BINDING_FIELDS = ("task_id", "revision", "contract_hash", "candidate")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a non-empty string")
    return value.strip()


def _revision(value):
    if isinstance(value, bool):
        raise ValueError("revision must be a positive integer")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("revision must be a positive integer") from None
    if value < 1:
        raise ValueError("revision must be a positive integer")
    return value


def _sha256(value, name):
    value = _text(value, name)
    if _HASH.fullmatch(value) is None:
        raise ValueError(name + " must be a 64-character hexadecimal digest")
    return value.lower()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _digest(receipt):
    payload = {key: value for key, value in receipt.items()
               if key != "receipt_sha256"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _created_at(value):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    return _text(value, "created_at")


def _artifact_refs(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("artifact_refs must be a list")
    result = []
    for index, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise ValueError("artifact_refs[%d] must be an object" % index)
        clean = dict(ref)
        clean["uri"] = _text(clean.get("uri"), "artifact_refs[%d].uri" % index)
        clean["sha256"] = _sha256(clean.get("sha256"),
                                   "artifact_refs[%d].sha256" % index)
        if "kind" in clean:
            clean["kind"] = _text(clean["kind"], "artifact_refs[%d].kind" % index)
        result.append(clean)
    return result


def _validate_shape(receipt):
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    if receipt.get("format") != FORMAT:
        raise ValueError("unsupported checkpoint receipt format")
    for field in ("task_id", "candidate", "resume_token", "checkpoint_id", "next_action"):
        _text(receipt.get(field), field)
    revision = _revision(receipt.get("revision"))
    contract_hash = _sha256(receipt.get("contract_hash"), "contract_hash")
    phase = _text(receipt.get("phase"), "phase")
    if phase not in PHASES:
        raise ValueError("phase must be one of " + ", ".join(PHASES))
    status = _text(receipt.get("status"), "status")
    if status not in STATUSES:
        raise ValueError("status must be one of " + ", ".join(STATUSES))
    if "cursor" not in receipt or receipt["cursor"] in (None, ""):
        raise ValueError("cursor is required")
    _revision(receipt.get("seq"))
    stopped = receipt.get("executor_stopped")
    if not isinstance(stopped, bool):
        raise ValueError("executor_stopped must be a boolean")
    if status == "PAUSED" and stopped is not True:
        raise ValueError("paused receipt requires executor_stopped=true")
    _created_at(receipt.get("created_at"))
    _artifact_refs(receipt.get("artifact_refs"))
    return {
        "task_id": _text(receipt["task_id"], "task_id"),
        "revision": revision,
        "contract_hash": contract_hash,
        "candidate": _text(receipt["candidate"], "candidate"),
    }


def create(payload):
    """Create a receipt from a host observation.

    ``payload`` is copied and normalized.  The caller supplies the cursor,
    resume token and stop proof; a missing timestamp is filled by the host
    process at creation time.  The returned hash covers every receipt field
    except itself.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    receipt = {
        "format": FORMAT,
        "task_id": _text(payload.get("task_id"), "task_id"),
        "revision": _revision(payload.get("revision")),
        "contract_hash": _sha256(payload.get("contract_hash"), "contract_hash"),
        "candidate": _text(payload.get("candidate"), "candidate"),
        "checkpoint_id": _text(payload.get("checkpoint_id", payload.get("id")), "checkpoint_id"),
        "seq": _revision(payload.get("seq")),
        "phase": _text(payload.get("phase"), "phase"),
        "status": _text(payload.get("status"), "status"),
        "cursor": payload.get("cursor"),
        "resume_token": _text(payload.get("resume_token"), "resume_token"),
        "executor_stopped": payload.get("executor_stopped"),
        "next_action": _text(payload.get("next_action"), "next_action"),
        "artifact_refs": _artifact_refs(payload.get("artifact_refs")),
        "created_at": _created_at(payload.get("created_at")),
    }
    if "metadata" in payload:
        if not isinstance(payload["metadata"], dict):
            raise ValueError("metadata must be an object")
        receipt["metadata"] = dict(payload["metadata"])
    _validate_shape(receipt)
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def validate(receipt, expected=None):
    """Return a structured validation result without mutating ``receipt``.

    ``expected`` may contain any of ``task_id``, ``revision``,
    ``contract_hash`` and ``candidate``.  Mismatches are reported as stale
    bindings; a valid hash alone does not make a receipt current for another
    task or candidate.
    """
    try:
        binding = _validate_shape(receipt)
    except (TypeError, ValueError) as error:
        return {"status": "INVALID", "errors": [str(error)], "receipt_sha256": None}
    declared = receipt.get("receipt_sha256")
    if not isinstance(declared, str) or _HASH.fullmatch(declared) is None:
        return {"status": "INVALID", "errors": ["receipt_sha256 is missing or invalid"],
                "receipt_sha256": None}
    actual = _digest(receipt)
    if declared.lower() != actual:
        return {"status": "INVALID", "errors": ["receipt hash mismatch"],
                "receipt_sha256": actual}
    if expected is not None:
        if not isinstance(expected, dict):
            return {"status": "INVALID", "errors": ["expected binding must be an object"],
                    "receipt_sha256": actual}
        stale = []
        for field in _BINDING_FIELDS:
            if field in expected and expected[field] != binding[field]:
                stale.append(field)
        if stale:
            return {"status": "STALE", "errors": ["binding mismatch: " + ", ".join(stale)],
                    "receipt_sha256": actual}
    return {"status": "VALID", "errors": [], "receipt_sha256": actual,
            "binding": binding, "next_action": receipt["next_action"]}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--input", required=True, type=Path,
                               help="JSON host observation")
    create_parser.add_argument("--output", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--receipt", required=True, type=Path)
    verify_parser.add_argument("--expected", type=Path,
                               help="optional JSON binding to compare")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create(_read_json(args.input))
            payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload, encoding="utf-8")
            print(payload, end="")
            return 0
        expected = _read_json(args.expected) if args.expected else None
        result = validate(_read_json(args.receipt), expected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "VALID" else 2
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "INVALID", "errors": [str(error)],
                          "receipt_sha256": None}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
