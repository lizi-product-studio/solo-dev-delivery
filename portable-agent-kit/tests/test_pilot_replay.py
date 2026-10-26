import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluations" / "autonomy-benchmark-2026-10-26"))
import replay_runner


def small_contract():
    return {
        "schema_version": "0.13", "contract_id": "replay-test", "objective": "test",
        "arms": [{"id": "baseline"}],
        "tasks": [{"id": "task-a", "objective": "do a", "risk": "low",
                    "required_capabilities": [], "expected_observations": [{"id": "done"}],
                    "stop_conditions": ["unknown"]}],
        "repetitions": 1,
        "metrics": ["final_success", "execution_seconds"],
        "freeze": {"seed": "x", "evaluator_sha256": "y"},
    }


class PilotReplayTests(unittest.TestCase):
    def receipt(self, root):
        evidence = root / "evidence.txt"
        evidence.write_text("observed\n", encoding="utf-8")
        ref = "evidence.txt"
        return {
            "schema_version": "0.13", "contract_id": "replay-test",
            "trial_id": "task-a__baseline__r1", "task_id": "task-a", "arm_id": "baseline", "repeat": 1,
            "status": "completed", "evidence_status": "DONE", "final_success": True,
            "execution_seconds": 1, "evidence_refs": [ref],
            "provenance": {"mode": "operator_replay", "host": "test-host", "skill_revision": "abc",
                            "started_at": "2026-10-26T00:00:00Z", "ended_at": "2026-10-26T00:00:01Z",
                            "run_id": "run-1", "task_objective": "do a"},
            "observations": {"done": {"status": "observed", "evidence_refs": [ref]}},
        }

    def test_plan_is_explicitly_planned_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); contract_path = root / "contract.json"; out = root / "plan.jsonl"
            contract_path.write_text(json.dumps(small_contract()), encoding="utf-8")
            self.assertEqual(0, replay_runner.main(["plan", str(contract_path), str(out)]))
            row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("planned", row["status"])
            self.assertEqual("task-a__baseline__r1", row["trial_id"])

    def test_real_receipt_requires_evidence_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); contract_path = root / "contract.json"; receipts = root / "receipts.jsonl"
            contract_path.write_text(json.dumps(small_contract()), encoding="utf-8")
            receipts.write_text(json.dumps(self.receipt(root)) + "\n", encoding="utf-8")
            result = replay_runner.validate_receipts(contract_path, receipts, root)
            self.assertEqual("COMPLETE", result["status"])
            self.assertEqual("VALID", result["provenance_status"])

    def test_synthetic_receipts_are_refused_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); contract_path = root / "contract.json"; receipts = root / "receipts.jsonl"
            contract_path.write_text(json.dumps(small_contract()), encoding="utf-8")
            row = self.receipt(root); row["provenance"]["mode"] = "synthetic_fixture"
            receipts.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(replay_runner.ReplayError):
                replay_runner.validate_receipts(contract_path, receipts, root)

    def test_evidence_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); contract_path = root / "contract.json"; receipts = root / "receipts.jsonl"
            contract_path.write_text(json.dumps(small_contract()), encoding="utf-8")
            row = self.receipt(root); row["evidence_refs"] = ["../outside.txt"]
            receipts.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(replay_runner.ReplayError):
                replay_runner.validate_receipts(contract_path, receipts, root)


if __name__ == "__main__":
    unittest.main()
