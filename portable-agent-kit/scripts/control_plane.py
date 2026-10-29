#!/usr/bin/env python3
"""Command line entry point for the portable delivery control plane.

The CLI persists task state and ownership in SQLite, but it never executes an
arbitrary shell command.  Hosts submit tool results and evidence through the
same AAP state gates after they perform the actual operation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_plane.runtime import ControlPlaneError, DeliveryRuntime


def _json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _json_arg(value):
    if value.startswith("@"):
        return _json_file(value[1:])
    return json.loads(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="SQLite control database")
    parser.add_argument("--evidence-root", type=Path, help="root for independent local evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--contract", type=Path, required=True)
    create.add_argument("--capabilities", type=Path, required=True)
    create.add_argument("--adapter-id", default="cli")
    create.add_argument("--host-id", default="local")

    claim = sub.add_parser("claim")
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--ttl-seconds", type=int, default=300)

    for name in ("plan", "recover", "events"):
        item = sub.add_parser(name)
        item.add_argument("--task-id", required=True)

    release = sub.add_parser("release")
    release.add_argument("--task-id", required=True)
    release.add_argument("--lease-id", required=True)

    event = sub.add_parser("event")
    event.add_argument("--task-id", required=True)
    event.add_argument("--lease-id", required=True)
    event.add_argument("--op", required=True)
    event.add_argument("--payload", default="{}", help="JSON or @path/to/json")
    event.add_argument("--request-id")
    event.add_argument("--reviewer-id")

    args = parser.parse_args(argv)
    runtime = DeliveryRuntime(args.db, evidence_root=args.evidence_root)
    if args.command == "create":
        result = runtime.create_task(_json_file(args.contract), _json_file(args.capabilities),
                                     adapter_id=args.adapter_id, host_id=args.host_id)
    elif args.command == "claim":
        result = runtime.claim(args.task_id, args.owner, ttl_seconds=args.ttl_seconds)
    elif args.command == "plan":
        result = runtime.plan(args.task_id)
    elif args.command == "recover":
        result = runtime.recover(args.task_id)
    elif args.command == "events":
        result = runtime.events(args.task_id)
    elif args.command == "release":
        result = runtime.release(args.task_id, args.lease_id)
    else:
        state, response = runtime.apply(
            args.task_id, args.lease_id, args.op, _json_arg(args.payload),
            request_id=args.request_id, evidence_root=args.evidence_root,
            reviewer_id=args.reviewer_id)
        result = {"state": state, "response": response}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ControlPlaneError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        raise SystemExit(2)
