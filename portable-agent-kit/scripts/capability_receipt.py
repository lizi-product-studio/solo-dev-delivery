#!/usr/bin/env python3
"""Create a deterministic, advisory host capability snapshot.

The snapshot records what a host declares; it does not probe tools, grant
authorization, or prove that a task completed.  ``unknown`` remains a
first-class state and therefore yields ``NEEDS_CONTEXT``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capabilities import normalize, requirements  # noqa: E402

FORMAT = "capability-snapshot/1"
STATES = ("supported", "unsupported", "unknown")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a non-empty string")
    return value.strip()


def _manifest_fields(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("capabilities manifest must be an object")
    caps = manifest.get("capabilities", manifest)
    if not isinstance(caps, dict):
        raise ValueError("manifest.capabilities must be an object")
    metadata = manifest.get("host", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("manifest.host must be an object")
    # Accept both a compact top-level manifest and a nested host object.
    values = {}
    for key in ("host_id", "host_version", "platform"):
        values[key] = manifest.get(key, metadata.get(key))
    return caps, values


def _canonical_hash(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create(manifest, required=(), *, host_id=None, host_version=None,
           platform=None, contract=None):
    caps, metadata = _manifest_fields(manifest)
    host_id = _text(host_id if host_id is not None else metadata.get("host_id"), "host_id")
    host_version = _text(host_version if host_version is not None else metadata.get("host_version"), "host_version")
    platform = _text(platform if platform is not None else metadata.get("platform"), "platform")
    required = requirements(contract, required)
    normalized = normalize(caps)
    observed = {name: normalized.get(name, "unknown") for name in required}
    unsupported = [name for name, state in observed.items() if state == "unsupported"]
    unknown = [name for name, state in observed.items() if state == "unknown"]
    status = "BLOCKED" if unsupported else ("NEEDS_CONTEXT" if unknown else "READY")
    snapshot = {
        "format": FORMAT,
        "host_id": host_id,
        "host_version": host_version,
        "platform": platform,
        "capabilities": {name: normalized[name] for name in sorted(normalized)},
        "required_capabilities": required,
        "status": status,
    }
    snapshot["snapshot_sha256"] = _canonical_hash(snapshot)
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", required=True, type=Path)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--host-id")
    parser.add_argument("--host-version")
    parser.add_argument("--platform")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.capabilities.read_text(encoding="utf-8-sig"))
        contract = json.loads(args.contract.read_text(encoding="utf-8-sig")) if args.contract else None
        result = create(manifest, args.require, host_id=args.host_id,
                        host_version=args.host_version, platform=args.platform,
                        contract=contract)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"format": FORMAT, "status": "NEEDS_CONTEXT", "errors": [str(error)],
                  "snapshot_sha256": None}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
