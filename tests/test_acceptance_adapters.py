import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable-agent-kit"))
from control_plane.adapters import ReceiptError, make_receipt, verify_receipt


class AcceptanceAdapterTests(unittest.TestCase):
    def state(self):
        return {"task_id": "t", "revision": 2, "contract_hash": "c", "candidate": "v", "owner": "owner"}

    def test_all_receipt_kinds_bind_and_hash(self):
        for kind in ("semantic", "ui", "business", "platform"):
            receipt = make_receipt(self.state(), kind, "reviewer", [{"check_id": "A", "status": "PASS"}])
            self.assertEqual("PASS", verify_receipt(receipt, self.state(), reviewer_id="reviewer", expected_kind=kind)["status"])
            self.assertEqual(64, len(receipt["receipt_sha256"]))

    def test_receipt_rejects_forged_reviewer_binding_and_partial_checks(self):
        receipt = make_receipt(self.state(), "business", "reviewer", [{"check_id": "A", "status": "PASS"}])
        with self.assertRaises(ReceiptError):
            verify_receipt({**receipt, "reviewer_id": "owner"}, self.state(), reviewer_id="reviewer", expected_kind="business")
        with self.assertRaises(ReceiptError):
            verify_receipt({**receipt, "checks": [{"check_id": "A", "status": "INCOMPLETE"}]}, self.state(), reviewer_id="reviewer", expected_kind="business")


if __name__ == "__main__":
    unittest.main()
