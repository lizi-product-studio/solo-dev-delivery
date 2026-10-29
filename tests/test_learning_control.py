import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable-agent-kit"))

from control_plane.learning import (LearningError, gate, normalize_record, propose,
                                    wake)


class LearningControlTests(unittest.TestCase):
    def test_proposal_requires_distinct_runs_and_hashed_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "evidence.log"
            evidence.write_text("failure evidence\n", encoding="utf-8")
            sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
            ledger = root / "ledger.jsonl"
            base = {"task_id": "task-1", "candidate": "c1", "status": "failure",
                    "failure_class": "tool", "root_cause": "bad adapter",
                    "message": "tool failed", "evidence_refs": [{"uri": "evidence.log", "sha256": sha}]}
            for run_id in ("run-1", "run-1"):
                row = dict(base, run_id=run_id)
                with ledger.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
            result = propose(ledger, "c2", "fix adapter", ["patch.diff"],
                             evidence_root=root)
            self.assertEqual("INSUFFICIENT_EVIDENCE", result["status"])
            with ledger.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(base, run_id="run-2")) + "\n")
            result = propose(ledger, "c2", "fix adapter", ["patch.diff"],
                             evidence_root=root)
            self.assertEqual("PROPOSED", result["status"])
            self.assertEqual(["run-1", "run-2"], result["targets"][0]["distinct_runs"])

    def test_gate_reads_bound_receipts_instead_of_accepting_status_strings(self):
        proposal = {"format": "self-improvement/2", "status": "PROPOSED",
                    "proposal_id": "p1", "candidate": "c2"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for name in ("review", "regression", "canary", "rollback"):
                path = root / (name + ".json")
                path.write_text(json.dumps({"status": "PASS", "proposal_id": "p1",
                                             "candidate": "c2", "evidence_refs": [
                                                 {"uri": name, "sha256": "a" * 64}]}),
                                encoding="utf-8")
                paths.append(path)
            result = gate(proposal, *paths)
            self.assertEqual("RELEASE_CANDIDATE", result["status"])
            bad = root / "bad.json"
            bad.write_text(json.dumps({"status": "PASS", "proposal_id": "other",
                                       "candidate": "c2", "evidence_refs": [
                                           {"uri": "x", "sha256": "a" * 64}]}),
                           encoding="utf-8")
            with self.assertRaisesRegex(LearningError, "binding mismatch"):
                gate(proposal, bad, paths[1], paths[2], paths[3])

    def test_wake_rejects_overlapping_authorization_and_pauses_unknown(self):
        with self.assertRaisesRegex(LearningError, "overlap"):
            wake("run", 1, 0, 3, "local", ["collect"], ["collect"])
        result = wake("run", 1, 0, 3, "local", ["collect"], ["publish"], True)
        self.assertEqual("PAUSED", result["status"])
        self.assertEqual("external_result_unknown", result["stop_reason"])


if __name__ == "__main__":
    unittest.main()
