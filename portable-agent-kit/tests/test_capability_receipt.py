import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import capability_receipt
import host_replay


class CapabilityReceiptTests(unittest.TestCase):
    def manifest(self):
        return {"host_id": "fixture-host", "host_version": "1.2",
                "platform": "test", "capabilities": {
                    "fs.read": True, "shell.exec": "unknown",
                    "browser.control": False}}

    def test_ready_snapshot_is_deterministic_and_hash_bound(self):
        first = capability_receipt.create(self.manifest(), ["fs.read"])
        second = capability_receipt.create(self.manifest(), ["fs.read"])
        self.assertEqual(first, second)
        self.assertEqual("capability-snapshot/1", first["format"])
        self.assertEqual("READY", first["status"])
        unsigned = dict(first); unsigned.pop("snapshot_sha256")
        expected = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(expected, first["snapshot_sha256"])

    def test_unsupported_blocks_and_unknown_needs_context(self):
        blocked = capability_receipt.create(self.manifest(), ["browser.control"])
        unknown = capability_receipt.create(self.manifest(), ["shell.exec"])
        missing = capability_receipt.create(self.manifest(), ["missing"])
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("NEEDS_CONTEXT", unknown["status"])
        self.assertEqual("NEEDS_CONTEXT", missing["status"])

    def test_cli_writes_snapshot_and_reports_non_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = root / "caps.json"; output = root / "snapshot.json"
            manifest.write_text(json.dumps(self.manifest()), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(KIT / "scripts/capability_receipt.py"),
                                   "--capabilities", str(manifest), "--require", "shell.exec",
                                   "--output", str(output)], text=True, capture_output=True)
            self.assertEqual(2, proc.returncode)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("NEEDS_CONTEXT", saved["status"])
            self.assertEqual(saved["snapshot_sha256"], json.loads(proc.stdout)["snapshot_sha256"])


class HostReplayCapabilityBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.evidence = self.root / "evidence"; self.evidence.mkdir()
        for phase in host_replay.PHASES: (self.evidence / (phase + ".log")).write_text(phase, encoding="utf-8")
        self.candidate = "git:fixture"; self.package = "a" * 64; self.capability = "b" * 64

    def receipt(self):
        rows = [{"id": phase, "phase": phase, "status": "PASS", "source": "host-command",
                 "evidence_refs": [{"path": phase + ".log", "sha256": hashlib.sha256((self.evidence / (phase + ".log")).read_bytes()).hexdigest()}]}
                for phase in host_replay.PHASES]
        return {"format": host_replay.FORMAT, "host_id": "h", "host_version": "1", "platform": "test",
                "candidate": self.candidate, "package_sha256": self.package, "observed_at": "now",
                "capability_sha256": self.capability, "required_capabilities": ["fs.read", "shell.exec"],
                "observations": rows}

    def test_binding_matches_and_stale_snapshot_rejects(self):
        receipt = self.receipt()
        result = host_replay.validate(receipt, evidence_root=self.evidence,
                                      expected_capability_sha256=self.capability,
                                      expected_required_capabilities=["shell.exec", "fs.read"])
        self.assertEqual("PASS", result["status"])
        with self.assertRaisesRegex(ValueError, "capability snapshot"):
            host_replay.validate(receipt, evidence_root=self.evidence, expected_capability_sha256="c" * 64)

    def test_non_ready_capability_snapshot_keeps_replay_incomplete(self):
        receipt = self.receipt(); receipt["capability_status"] = "NEEDS_CONTEXT"
        result = host_replay.validate(receipt, evidence_root=self.evidence)
        self.assertEqual("INCOMPLETE", result["status"])
        self.assertEqual("NEEDS_CONTEXT", result["capability_status"])

    def test_old_receipt_without_binding_remains_compatible(self):
        receipt = self.receipt(); receipt.pop("capability_sha256"); receipt.pop("required_capabilities")
        self.assertEqual("PASS", host_replay.validate(receipt, evidence_root=self.evidence)["status"])


if __name__ == "__main__":
    unittest.main()
