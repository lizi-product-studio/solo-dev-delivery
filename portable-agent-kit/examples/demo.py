"""Run the same simple task through the provider-neutral core."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.state_machine import apply, start

contract = {
    "id": "T-demo-001", "goal": "修正隔离页面文案", "owner": "agent/demo",
    "authorization_ref": "demo-authorized", "candidate": "demo-rev-1",
    "scope": ["src/page.txt"], "excluded": ["发布", "新增费用"],
    "acceptance": [{"id": "AC1", "expected": "写入后读回内容一致", "environment": "isolated_workspace"}],
    "policy": {"workspace_write": True, "ux_required": False, "external_actions": False},
}
capabilities = {"fs.read": True, "fs.write": True, "shell.exec": True,
                "evidence.hash": True, "checkpoint.persist": True}
state = start(contract, capabilities, adapter_id="generic-demo", host_id="demo-host")

def send(op, key, payload=None):
    global state
    request = {"protocol": "aap/0.1", "request_id": "r-" + key, "op": op,
               "task_id": state["task_id"], "revision": state["revision"],
               "contract_hash": state["contract_hash"], "candidate": state["candidate"],
               "idempotency_key": key, "payload": payload or {}}
    state, response = apply(state, request)
    print(json.dumps(response, ensure_ascii=False))

send("start", "start", {"executor": "agent/demo"})
send("tool_result", "write", {"tool_call_id": "write-1", "status": "SUCCEEDED"})
send("tool_result", "readback", {"tool_call_id": "read-1", "status": "SUCCEEDED", "observed_state": {"matches": True}})
send("evidence", "evidence", {"id": "ev-demo", "kind": "readback", "uri": "memory://ev-demo",
    "sha256": "0" * 64, "claim": "写入后读回内容一致", "verdict": "PASS",
    "task_id": state["task_id"], "revision": state["revision"],
    "contract_hash": state["contract_hash"], "candidate": state["candidate"]})
send("phase", "review", {"phase": "review"})
send("finish", "finish", {"executor_stopped": True})
