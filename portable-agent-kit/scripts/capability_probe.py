#!/usr/bin/env python3
"""Optional stateless preflight. Does not execute tools or create a session."""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capabilities import preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", required=True, type=Path)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--contract", type=Path, help="Optional requirements only; not a delivery verdict")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.capabilities.read_text(encoding="utf-8-sig"))
        contract = json.loads(args.contract.read_text(encoding="utf-8-sig")) if args.contract else None
        result = preflight(manifest, args.require, contract)
    except (ValueError, OSError) as error:
        result = {"status": "NEEDS_CONTEXT", "errors": [str(error)], "advisory": True,
                  "tools_executed": False, "authorization_granted": False}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Status: " + result["status"])
        for field in ("required", "supported", "unsupported", "unknown", "errors"):
            print(field + ": " + (", ".join(result.get(field, [])) or "none"))
        print("Advisory only; tools, evidence and authorization must be verified by the host.")
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
