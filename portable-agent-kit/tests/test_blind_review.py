import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import blind_review


def make_contract():
    return {
        "schema_version": "0.13", "contract_id": "pilot-test", "objective": "verify",
        "arms": [{"id": "baseline"}, {"id": "candidate"}],
        "tasks": [{"id": "task-a", "objective": "do it", "expected_observations": [{"id": "changed", "description": "changed"}, {"id": "readback", "description": "readback"}], "risk": "low", "required_capabilities": [], "stop_conditions": ["unknown"]}],
        "repetitions": 1, "metrics": ["final_success"],
        "freeze": {"seed": "test", "evaluator_sha256": "e" * 64},
    }


def write_fixture(root):
    contract_path = root / "contract.json"
    receipts_path = root / "receipts.jsonl"
    contract = make_contract()
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    contract_sha = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    candidate_sha = "c" * 64
    refs = [{"id": "artifact-1", "sha256": "a" * 64, "trial_id": "trial-1", "contract_id": "pilot-test", "source_revision": "rev-1", "candidate_sha256": candidate_sha},
            {"id": "artifact-2", "sha256": "b" * 64, "trial_id": "trial-1", "contract_id": "pilot-test", "source_revision": "rev-1", "candidate_sha256": candidate_sha}]
    receipt = {"schema_version": "0.13", "contract_id": "pilot-test", "contract_sha256": contract_sha, "trial_id": "trial-1", "task_id": "task-a", "arm_id": "baseline", "repeat": 1, "status": "completed", "evidence_status": "DONE", "final_success": True, "evidence_refs": refs, "source_revision": "rev-1", "candidate_sha256": candidate_sha}
    receipts_path.write_text(json.dumps(receipt, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract, contract_path, receipts_path, receipt


def review_for(contract, receipt, contract_path):
    return {"schema_version": "0.1", "contract_id": contract["contract_id"], "packet_id": "packet-x", "trial_id": receipt["trial_id"], "task_id": receipt["task_id"], "repeat": 1, "receipt_sha256": blind_review.digest_value(receipt), "reviewer_id": "reviewer-a", "blind": True, "evaluator_sha256": contract["freeze"]["evaluator_sha256"], "final_verdict": "PASS", "observations": {"changed": {"verdict": "PASS", "evidence_refs": ["artifact-1"]}, "readback": {"verdict": "PASS", "evidence_refs": ["artifact-2"]}}}


class BlindReviewTests(unittest.TestCase):
    def test_packets_are_blind_and_mapping_retains_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract, contract_path, receipts_path, receipt = write_fixture(root)
            packets, mapping = blind_review.make_packets(contract, contract_path, receipts_path)
            self.assertEqual(1, len(packets)); self.assertEqual(1, len(mapping))
            self.assertNotIn("arm_id", packets[0]); self.assertNotIn("source_revision", packets[0]); self.assertEqual("baseline", mapping[0]["arm_id"])
            self.assertTrue(packets[0]["blind"])

    def test_valid_review_binds_receipt_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract, contract_path, receipts_path, receipt = write_fixture(root)
            reviews_path = root / "reviews.jsonl"
            reviews_path.write_text(json.dumps(review_for(contract, receipt, contract_path), ensure_ascii=False) + "\n", encoding="utf-8")
            result = blind_review.validate_reviews(contract, contract_path, receipts_path, reviews_path)
            self.assertEqual("COMPLETE", result["status"])

    def test_stale_receipt_contract_or_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract, contract_path, receipts_path, receipt = write_fixture(root)
            receipt["source_revision"] = "old-rev"
            receipts_path.write_text(json.dumps(receipt, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(blind_review.ReviewError):
                blind_review.validate_receipts_for_review(contract, contract_path, receipts_path)

    def test_review_rejects_digest_or_arm_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract, contract_path, receipts_path, receipt = write_fixture(root)
            row = review_for(contract, receipt, contract_path)
            row["arm_id"] = "baseline"
            reviews_path = root / "reviews.jsonl"
            reviews_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(blind_review.ReviewError):
                blind_review.validate_reviews(contract, contract_path, receipts_path, reviews_path)

    def test_unknown_observation_requires_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract, contract_path, receipts_path, receipt = write_fixture(root)
            row = review_for(contract, receipt, contract_path)
            row["final_verdict"] = "UNKNOWN"
            row["observations"]["changed"] = {"verdict": "UNKNOWN", "evidence_refs": []}
            reviews_path = root / "reviews.jsonl"
            reviews_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(blind_review.ReviewError):
                blind_review.validate_reviews(contract, contract_path, receipts_path, reviews_path)


if __name__ == "__main__":
    unittest.main()
