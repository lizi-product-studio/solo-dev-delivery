import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ExampleAdapterTests(unittest.TestCase):
    def test_reference_adapter_can_probe_and_start_over_jsonl(self):
        contract = {"id": "T-adapter", "goal": "fixture", "owner": "agent/test",
                    "authorization_ref": "test", "candidate": "candidate",
                    "scope": ["fixture"], "excluded": ["external"],
                    "acceptance": [{"id": "AC1", "expected": "readback", "environment": "isolated"}],
                    "policy": {"workspace_write": False, "ux_required": False, "external_actions": False}}
        lines = [
            {"op": "start", "contract": contract, "host": {"adapter_id": "test", "host_id": "host", "capabilities": {"evidence.hash": True, "checkpoint.persist": True}}},
        ]
        proc = subprocess.run([sys.executable, str(ROOT / "adapters/example_adapter.py")],
                              input="\n".join(json.dumps(x) for x in lines) + "\n",
                              text=True, capture_output=True, check=True)
        state = json.loads(proc.stdout)
        self.assertEqual("READY", state["status"])
        self.assertEqual("aap/0.1", state["protocol"])

    def test_probe_does_not_replace_state_and_second_init_cannot_reset_task(self):
        sys.path.insert(0, str(ROOT))
        from adapters.example_adapter import handle
        from core.state_machine import ProtocolError
        contract = {"id":"T", "goal":"g", "owner":"h", "authorization_ref":"local",
                    "candidate":"c", "scope":["a"], "excluded":["external"],
                    "acceptance":[{"id":"A", "expected":"e", "environment":"local"}],
                    "policy":{"workspace_write":False}}
        initial = {"op":"start", "contract":contract, "host":{"capabilities":{
            "evidence.hash":True, "checkpoint.persist":True}}}
        state = handle(initial)
        request = {"protocol":"aap/0.1", "request_id":"s", "op":"start", "task_id":"T",
                   "revision":1, "contract_hash":state["contract_hash"], "candidate":"c",
                   "idempotency_key":"s", "payload":{}}
        rows = [initial, {"op":"probe","host":{}}, request, initial]
        proc = subprocess.run([sys.executable, str(ROOT / "adapters/example_adapter.py")],
            input="\n".join(json.dumps(x) for x in rows)+"\n", text=True, capture_output=True, check=True)
        results = [json.loads(x) for x in proc.stdout.splitlines()]
        self.assertEqual("RUNNING", results[2]["status"])
        self.assertFalse(results[3]["ok"])
        with self.assertRaises(ProtocolError):
            handle(initial, state)


if __name__ == "__main__":
    unittest.main()
