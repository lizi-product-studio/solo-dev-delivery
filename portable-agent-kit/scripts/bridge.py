#!/usr/bin/env python3
"""Bridge a real Agent/Skill session into the local control plane."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control_plane.runtime import DeliveryRuntime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--owner", default="codex-agent")
    parser.add_argument("--candidate", default="live")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--scope", action="append", default=["current-task"])
    parser.add_argument("--excluded", action="append", default=["production", "external-send"])
    parser.add_argument("--capabilities", type=Path)
    args = parser.parse_args(argv)
    session_id = args.session_id or "session-" + uuid.uuid4().hex
    task_id = "task-" + uuid.uuid4().hex[:12]
    contract = {
        "format": "delivery-contract/1", "task_id": task_id, "goal": args.goal,
        "owner": args.owner, "candidate": args.candidate,
        "authorization_ref": "codex-session:" + session_id,
        "session_id": session_id, "scope": args.scope, "excluded": args.excluded,
        "policy": {"workspace_write": True, "external_actions": False, "ux_required": False},
        "acceptance": [{"id": "AGI-LIKE-DELIVERY", "expected": "原始目标在真实入口完成并读回", "environment": "local", "assertion": {"type": "file_exists"}}],
    }
    capabilities = json.loads(args.capabilities.read_text(encoding="utf-8")) if args.capabilities else {
        "fs.read": True, "fs.write": True, "shell.exec": True,
        "evidence.hash": True, "checkpoint.persist": True,
    }
    state = DeliveryRuntime(args.db).create_task(contract, capabilities,
                                                   adapter_id="codex-skill-bridge",
                                                   host_id="codex")
    print(json.dumps({"status": "REGISTERED", "task_id": state["task_id"],
                      "session_id": session_id, "state": state}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
