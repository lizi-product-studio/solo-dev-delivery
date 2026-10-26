import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import host_replay


class HostReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        (self.evidence / "install.log").write_text("installed\n", encoding="utf-8")
        (self.evidence / "load.log").write_text("loaded\n", encoding="utf-8")
        (self.evidence / "invoke.log").write_text("invoked\n", encoding="utf-8")
        (self.evidence / "route.log").write_text("BOUNDED\n", encoding="utf-8")
        (self.evidence / "verify.log").write_text("readback pass\n", encoding="utf-8")
        self.candidate = "git:d608e92"
        self.package = "a" * 64

    def receipt(self):
        rows = []
        for phase in host_replay.PHASES:
            path = phase + ".log"
            rows.append({"id": phase, "phase": phase, "status": "PASS",
                          "source": "host-command", "evidence_refs": [
                              {"path": path, "sha256": hashlib.sha256((self.evidence / path).read_bytes()).hexdigest()}],
                          "candidate": self.candidate, "package_sha256": self.package})
        return {"format": host_replay.FORMAT, "host_id": "fixture-host", "host_version": "1",
                "platform": "test", "candidate": self.candidate, "package_sha256": self.package,
                "observed_at": "2026-10-26T00:00:00Z", "observations": rows}

    def test_valid_receipt_requires_all_bound_phases(self):
        result = host_replay.validate(self.receipt(), evidence_root=self.evidence,
                                      expected_package_sha256=self.package,
                                      expected_candidate=self.candidate)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["missing_phases"], [])

    def test_unknown_phase_is_incomplete_and_not_zero(self):
        receipt = self.receipt()
        receipt["observations"][-1]["status"] = "UNKNOWN"
        receipt["observations"][-1]["reason"] = "host did not expose readback"
        result = host_replay.validate(receipt, evidence_root=self.evidence)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIn("verify", result["incomplete_phases"])

    def test_pass_without_evidence_is_rejected(self):
        receipt = self.receipt()
        receipt["observations"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(ValueError, "non-empty evidence_refs"):
            host_replay.validate(receipt, evidence_root=self.evidence)

    def test_stale_candidate_is_rejected(self):
        receipt = self.receipt()
        receipt["observations"][0]["candidate"] = "git:old"
        with self.assertRaisesRegex(ValueError, "candidate"):
            host_replay.validate(receipt, evidence_root=self.evidence)

    def test_cli_returns_nonzero_for_unknown(self):
        receipt = self.receipt()
        receipt["observations"][-1]["status"] = "UNKNOWN"
        receipt["observations"][-1]["reason"] = "not exposed"
        receipt_path = self.root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(KIT / "scripts/host_replay.py"), str(receipt_path),
                              "--evidence-root", str(self.evidence)], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["status"], "INCOMPLETE")

    def test_parent_traversal_and_symlink_evidence_are_rejected(self):
        receipt = self.receipt()
        receipt["observations"][0]["evidence_refs"][0]["path"] = "../evidence/install.log"
        with self.assertRaisesRegex(ValueError, "relative|parent traversal"):
            host_replay.validate(receipt, evidence_root=self.evidence)
        link = self.evidence / "link.log"
        try:
            link.symlink_to(self.evidence / "install.log")
        except OSError as exc:
            self.skipTest("symlink creation unavailable: " + str(exc))
        receipt = self.receipt()
        receipt["observations"][0]["evidence_refs"][0] = {
            "path": "link.log", "sha256": hashlib.sha256((self.evidence / "install.log").read_bytes()).hexdigest()}
        with self.assertRaisesRegex(ValueError, "symlinks"):
            host_replay.validate(receipt, evidence_root=self.evidence)


if __name__ == "__main__":
    unittest.main()
