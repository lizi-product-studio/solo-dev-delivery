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


if __name__ == "__main__":
    unittest.main()
