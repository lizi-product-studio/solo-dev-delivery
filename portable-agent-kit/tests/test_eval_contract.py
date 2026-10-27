import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import eval_contract


def contract():
    return {
        "schema_version": "0.13",
        "contract_id": "test-contract",
        "objective": "test objective",
        "arms": [{"id": "baseline"}, {"id": "candidate"}],
        "tasks": [{"id": "task-a", "objective": "do it", "expected_observations": [{"id": "done"}],
                   "risk": "low", "required_capabilities": [], "stop_conditions": ["unknown"]}],
        "repetitions": 1,
        "metrics": ["first_pass_success", "final_success", "user_correction_count", "execution_seconds"],
        "freeze": {"seed": "test", "evaluator_sha256": "evaluator"},
    }


class EvaluationContractTests(unittest.TestCase):
    def test_semantic_decisions_map_to_existing_runtime_states(self):
        mapping = eval_contract.decision_map("MAINTENANCE", "DEFER", "BUSINESS_DECISION")
        self.assertIsNone(mapping["route"])
        self.assertEqual("NEEDS_CONTEXT", mapping["boundary"])
        self.assertEqual("NEEDS_INPUT", mapping["evidence"])

    def test_contract_validation_and_semantic_names(self):
        meta = eval_contract.validate_contract(contract())
        self.assertEqual(["baseline", "candidate"], meta["arms"])
        self.assertEqual(["task-a"], meta["tasks"])

    def test_invalid_duplicate_observation_is_rejected(self):
        c = contract()
        c["tasks"][0]["expected_observations"].append({"id": "done"})
        with self.assertRaises(eval_contract.ContractError):
            eval_contract.validate_contract(c)

    def test_unknown_values_are_preserved_and_incomplete_is_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "contract.json"
            receipts_path = root / "receipts.jsonl"
            contract_path.write_text(json.dumps(contract(), ensure_ascii=False), encoding="utf-8")
            row = {"schema_version": "0.13", "contract_id": "test-contract", "trial_id": "t1",
                   "task_id": "task-a", "arm_id": "baseline", "repeat": 1, "status": "unknown",
                   "evidence_status": "NEEDS_EVIDENCE", "first_pass_success": None,
                   "final_success": None, "evidence_refs": []}
            receipts_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            rows = eval_contract.validate_receipts(contract(), receipts_path)
            summary = eval_contract.summarize(contract(), rows)
            self.assertEqual("INCOMPLETE", summary["status"])
            self.assertIsNone(summary["arms"]["baseline"]["final_success"]["value"])
            self.assertEqual(1, summary["arms"]["baseline"]["final_success"]["unknown"])

    def test_success_requires_evidence_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipts.jsonl"
            row = {"schema_version": "0.13", "contract_id": "test-contract", "trial_id": "t1",
                   "task_id": "task-a", "arm_id": "baseline", "repeat": 1, "status": "completed",
                   "evidence_status": "DONE", "final_success": True, "evidence_refs": []}
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(eval_contract.ContractError):
                eval_contract.validate_receipts(contract(), path)

    def test_receipt_hash_binds_explicit_nondefault_contract_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "pilot-contract.json"
            default = root / "contract.json"
            frozen.write_text(json.dumps(contract(), ensure_ascii=False), encoding="utf-8")
            default.write_text(json.dumps({"different": True}), encoding="utf-8")
            row = {"schema_version": "0.13", "contract_id": "test-contract", "trial_id": "t1",
                   "task_id": "task-a", "arm_id": "baseline", "repeat": 1, "status": "unknown",
                   "evidence_status": "NEEDS_EVIDENCE", "final_success": None, "evidence_refs": [],
                   "contract_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest()}
            receipts = root / "receipts.jsonl"
            receipts.write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertEqual(1, len(eval_contract.validate_receipts(contract(), receipts, contract_path=frozen)))
