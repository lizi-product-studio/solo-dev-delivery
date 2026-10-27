#!/usr/bin/env python3
"""Validate host loading/replay receipts without claiming host compatibility.

This checker only binds observations to the current candidate and hashes supplied
raw evidence. It does not execute a host, interpret screenshots, or turn a model
self-report into proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

FORMAT = "host-replay-receipt/1"
PHASES = ("install", "load", "invoke", "route", "verify")
STATUSES = ("PASS", "FAIL", "UNKNOWN", "UNVERIFIED")
SOURCES = ("host-command", "host-log", "ui-observation", "artifact-readback", "independent-review")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _fail(message):
    raise ValueError(message)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        _fail(name + " must be a non-empty string")
    return value


def _hash_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        _fail("evidence path must be relative")
    if ".." in Path(relative).parts:
        _fail("evidence path must not contain parent traversal")
    candidate = root / relative
    # Check the lexical path before resolving: resolve() would erase a symlink
    # marker and make a redirected evidence file look ordinary.
    for item in (candidate, *candidate.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if item == root:
            break
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("evidence path cannot use symlinks or reparse points: " + relative)
    path = candidate.resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        _fail("evidence path escapes evidence root: " + relative)
    if not path.is_file() or path.is_symlink():
        _fail("evidence path must be a regular file: " + relative)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": relative.replace("\\", "/"), "sha256": digest}


def _evidence(root, value):
    if not isinstance(value, list) or not value:
        _fail("PASS/FAIL observations need non-empty evidence_refs")
    result = []
    for row in value:
        if not isinstance(row, dict):
            _fail("evidence_refs entries must be objects")
        path = _text(row.get("path"), "evidence_refs.path")
        expected = row.get("sha256")
        if not isinstance(expected, str) or not HEX64.fullmatch(expected):
            _fail("evidence_refs.sha256 must be a lowercase SHA-256")
        observed = _hash_file(root, path)
        if observed["sha256"] != expected:
            _fail("evidence hash mismatch: " + path)
        result.append(observed)
    return result


def validate(receipt, *, evidence_root=None, expected_package_sha256=None,
             expected_candidate=None, required_phases=PHASES,
             expected_capability_sha256=None, expected_required_capabilities=None):
    if not isinstance(receipt, dict):
        _fail("receipt must be an object")
    if receipt.get("format") != FORMAT:
        _fail("receipt.format must be " + FORMAT)
    for field in ("host_id", "host_version", "platform", "candidate", "package_sha256", "observed_at"):
        _text(receipt.get(field), "receipt." + field)
    if not HEX64.fullmatch(receipt["package_sha256"]):
        _fail("receipt.package_sha256 must be a lowercase SHA-256")
    if expected_package_sha256 is not None and receipt["package_sha256"] != expected_package_sha256:
        _fail("receipt is bound to a different package hash")
    if expected_candidate is not None and receipt["candidate"] != expected_candidate:
        _fail("receipt is bound to a different candidate")
    capability_sha256 = receipt.get("capability_sha256")
    if capability_sha256 is not None:
        if not isinstance(capability_sha256, str) or not HEX64.fullmatch(capability_sha256):
            _fail("receipt.capability_sha256 must be a lowercase SHA-256")
    if expected_capability_sha256 is not None:
        if not isinstance(expected_capability_sha256, str) or not HEX64.fullmatch(expected_capability_sha256):
            _fail("expected_capability_sha256 must be a lowercase SHA-256")
        if capability_sha256 is None:
            _fail("receipt is missing capability_sha256")
        if capability_sha256 != expected_capability_sha256:
            _fail("receipt is bound to a different capability snapshot")
    required_capabilities = receipt.get("required_capabilities")
    if required_capabilities is not None:
        if (not isinstance(required_capabilities, list) or
                any(not isinstance(item, str) or not item.strip() for item in required_capabilities) or
                required_capabilities != sorted(set(required_capabilities))):
            _fail("receipt.required_capabilities must be a sorted unique list of names")
    if expected_required_capabilities is not None:
        expected_required_capabilities = sorted(set(expected_required_capabilities))
        if required_capabilities is None:
            _fail("receipt is missing required_capabilities")
        if required_capabilities != expected_required_capabilities:
            _fail("receipt required_capabilities do not match expected requirements")
    capability_status = receipt.get("capability_status")
    if capability_status is not None and capability_status not in ("READY", "BLOCKED", "NEEDS_CONTEXT"):
        _fail("receipt.capability_status is invalid")
    phases = tuple(required_phases)
    if not phases or any(p not in PHASES for p in phases):
        _fail("required_phases must use known host replay phases")
    observations = receipt.get("observations")
    if not isinstance(observations, list) or not observations:
        _fail("receipt.observations must be a non-empty list")
    root = Path(evidence_root).resolve(strict=True) if evidence_root is not None else None
    if root is not None and not root.is_dir():
        _fail("evidence root must be a directory")
    seen = set()
    by_phase = {p: [] for p in phases}
    normalized = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            _fail("observation %d must be an object" % index)
        oid = _text(observation.get("id"), "observation.id")
        if oid in seen:
            _fail("duplicate observation id: " + oid)
        seen.add(oid)
        phase = observation.get("phase")
        if phase not in PHASES:
            _fail("observation.phase must be one of " + ", ".join(PHASES))
        status = observation.get("status")
        if status not in STATUSES:
            _fail("observation.status must be one of " + ", ".join(STATUSES))
        source = observation.get("source")
        if source not in SOURCES:
            _fail("observation.source must be an attributable host source")
        if observation.get("candidate", receipt["candidate"]) != receipt["candidate"]:
            _fail("observation candidate does not match receipt")
        if observation.get("package_sha256", receipt["package_sha256"]) != receipt["package_sha256"]:
            _fail("observation package hash does not match receipt")
        refs = observation.get("evidence_refs", [])
        if status in ("PASS", "FAIL"):
            if root is None:
                _fail("evidence_root is required for PASS/FAIL observations")
            refs = _evidence(root, refs)
        elif refs:
            if root is None:
                _fail("evidence_root is required when evidence_refs are supplied")
            refs = _evidence(root, refs)
        if status in ("UNKNOWN", "UNVERIFIED"):
            _text(observation.get("reason"), "UNKNOWN/UNVERIFIED observation.reason")
        normalized_row = dict(observation)
        normalized_row["evidence_refs"] = refs
        normalized.append(normalized_row)
        if phase in by_phase:
            by_phase[phase].append(status)
    missing = [phase for phase in phases if not by_phase[phase]]
    failed = [phase for phase in phases if "FAIL" in by_phase[phase]]
    incomplete = [phase for phase in phases if not any(x == "PASS" for x in by_phase[phase])]
    if missing or failed:
        overall = "FAIL" if failed else "INCOMPLETE"
    elif incomplete:
        overall = "INCOMPLETE"
    elif capability_status is not None and capability_status != "READY":
        overall = "INCOMPLETE"
    else:
        overall = "PASS"
    result = {"format": FORMAT, "status": overall, "host_id": receipt["host_id"],
            "candidate": receipt["candidate"], "package_sha256": receipt["package_sha256"],
            "required_phases": list(phases), "missing_phases": missing,
            "incomplete_phases": incomplete, "failed_phases": failed,
            "observations": normalized,
            "limits": "Evidence binding only; host loading, route semantics, UI meaning and business acceptance still require independent review."}
    if capability_sha256 is not None:
        result["capability_sha256"] = capability_sha256
    if required_capabilities is not None:
        result["required_capabilities"] = required_capabilities
    if capability_status is not None:
        result["capability_status"] = capability_status
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-package-sha256")
    parser.add_argument("--expected-candidate")
    parser.add_argument("--expected-capability-sha256")
    parser.add_argument("--require-capability", action="append", dest="required_capabilities")
    parser.add_argument("--require-phase", action="append", dest="phases")
    args = parser.parse_args(argv)
    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
        result = validate(receipt, evidence_root=args.evidence_root,
                          expected_package_sha256=args.expected_package_sha256,
                          expected_candidate=args.expected_candidate,
                          required_phases=tuple(args.phases or PHASES),
                          expected_capability_sha256=args.expected_capability_sha256,
                          expected_required_capabilities=args.required_capabilities)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"format": FORMAT, "status": "INVALID", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
