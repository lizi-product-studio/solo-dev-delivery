#!/usr/bin/env python3
"""Verify a persisted task snapshot with a reviewer distinct from its owner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_plane.evaluator import EvaluationError, verify_task
from control_plane.contracts import normalize_contract


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        state = json.loads(args.state.read_text(encoding="utf-8-sig"))
        contract = normalize_contract(json.loads(args.contract.read_text(encoding="utf-8-sig")))
        result = verify_task(state, contract, args.evidence_root,
                             reviewer_id=args.reviewer_id)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 2
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, EvaluationError) as exc:
        print(json.dumps({"format": "independent-review/1", "status": "INVALID",
                          "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
