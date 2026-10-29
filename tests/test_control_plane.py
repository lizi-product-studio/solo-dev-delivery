import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "portable-agent-kit"
sys.path.insert(0, str(KIT))

from control_plane.contracts import ContractError, normalize_contract
from control_plane.evaluator import EvaluationError, verify_task
from control_plane.runtime import ControlPlaneError, DeliveryRuntime


CAPABILITIES = {"evidence.hash": True, "checkpoint.persist": True,
                "fs.read": True}


class ControlPlaneTests(unittest.TestCase):
    def contract(self, evidence_hash):
        return {
            "format": "delivery-contract/1",
            "task_id": "task-control-1",
            "goal": "verify one local artifact",
            "owner": "planner",
            "authorization": {"ref": "local-test", "external_actions": False},
            "candidate": "candidate-1",
            "scope": ["result.txt"],
            "excluded": ["production", "external-send"],
            "risk": {"level": "low", "reversible": True, "impacts": ["local"]},
            "budget": {"max_steps": 5, "max_retries": 1},
            "stop_conditions": ["unknown side effect", "budget exhausted"],
            "acceptance": [{
                "id": "AC1", "expected": "artifact is exact", "environment": "isolated",
                "assertion": {"type": "file_sha256", "sha256": evidence_hash},
            }],
            "policy": {"workspace_write": False, "ux_required": False,
                       "external_actions": False},
        }

    def test_contract_normalizes_and_rejects_ambiguous_boundaries(self):
        normalized = normalize_contract({
            "task_id": "t", "goal": "g", "owner": "o", "candidate": "c",
            "authorization_ref": "a", "scope": ["x"], "excluded": ["y"],
            "acceptance": [{"id": "a1", "expected": "e", "environment": "isolated"}],
        })
        self.assertEqual("delivery-contract/1", normalized["format"])
        self.assertEqual("t", normalized["id"])
        self.assertEqual(20, normalized["budget"]["max_steps"])
        with self.assertRaises(ContractError):
            normalize_contract({**normalized, "acceptance": [normalized["acceptance"][0], normalized["acceptance"][0]]})

    def test_runtime_persists_lease_state_and_independent_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            content = b"verified artifact\n"
            (evidence_root / "result.txt").write_bytes(content)
            sha = hashlib.sha256(content).hexdigest()
            runtime = DeliveryRuntime(root / "control.sqlite3", evidence_root=evidence_root)
            contract = self.contract(sha)
            state = runtime.create_task(contract, CAPABILITIES,
                                        adapter_id="test-adapter", host_id="test-host")
            self.assertEqual("READY", state["status"])
            lease = runtime.claim(state["task_id"], "runner")
            running, response = runtime.apply(state["task_id"], lease["lease_id"], "start",
                                              {"executor": "runner"})
            self.assertEqual("RUNNING", running["status"])
            ref = {
                "id": "ev-1", "kind": "file", "uri": "result.txt", "sha256": sha,
                "claim": "exact", "verdict": "PASS", "check_id": "AC1",
                "environment": "isolated", "task_id": running["task_id"],
                "revision": running["revision"], "contract_hash": running["contract_hash"],
                "candidate": running["candidate"],
            }
            recorded, _ = runtime.apply(state["task_id"], lease["lease_id"], "evidence", ref,
                                        evidence_root=evidence_root, reviewer_id="reviewer")
            self.assertTrue(recorded["evidence"][0]["host_verified"])
            finished, response = runtime.apply(state["task_id"], lease["lease_id"], "finish",
                                               {"executor_stopped": True},
                                               evidence_root=evidence_root, reviewer_id="reviewer")
            self.assertEqual("COMPLETED", finished["status"])
            self.assertEqual("COMPLETED", response["status"])
            self.assertGreaterEqual(len(runtime.events(state["task_id"])), 4)

    def test_runtime_rejects_owner_as_independent_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            content = b"x"
            (evidence_root / "result.txt").write_bytes(content)
            sha = hashlib.sha256(content).hexdigest()
            runtime = DeliveryRuntime(root / "control.sqlite3", evidence_root=evidence_root)
            state = runtime.create_task(self.contract(sha), CAPABILITIES)
            lease = runtime.claim(state["task_id"], "runner")
            state, _ = runtime.apply(state["task_id"], lease["lease_id"], "start", {})
            ref = {"id": "ev", "kind": "file", "uri": "result.txt", "sha256": sha,
                   "claim": "x", "verdict": "PASS", "check_id": "AC1",
                   "environment": "isolated", "task_id": state["task_id"],
                   "revision": state["revision"], "contract_hash": state["contract_hash"],
                   "candidate": state["candidate"]}
            recorded, _ = runtime.apply(state["task_id"], lease["lease_id"], "evidence", ref,
                                        evidence_root=evidence_root, reviewer_id="planner")
            self.assertFalse(recorded["evidence"][0]["host_verified"])
            self.assertEqual("BLOCKED", recorded["status"])

    def test_lease_conflict_and_expiry_are_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = DeliveryRuntime(Path(temp) / "control.sqlite3")
            state = runtime.create_task({
                "task_id": "lease-task", "goal": "g", "owner": "a", "candidate": "c",
                "authorization_ref": "local", "scope": ["x"], "excluded": ["y"],
                "policy": {"workspace_write": False},
                "acceptance": [{"id": "AC1", "expected": "e", "environment": "isolated"}],
            }, CAPABILITIES)
            lease = runtime.claim(state["task_id"], "one", ttl_seconds=1)
            with self.assertRaisesRegex(ControlPlaneError, "TASK_ALREADY_LEASED"):
                runtime.claim(state["task_id"], "two", ttl_seconds=1)
            import time
            time.sleep(1.1)
            self.assertEqual("RECOVERY_REQUIRED", runtime.recover(state["task_id"])["status"])

    def test_heartbeat_and_reclaim_require_live_or_stop_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = DeliveryRuntime(Path(temp) / "control.sqlite3")
            state = runtime.create_task({
                "task_id": "reclaim-task", "goal": "g", "owner": "a", "candidate": "c",
                "authorization_ref": "local", "scope": ["x"], "excluded": ["y"],
                "policy": {"workspace_write": False},
                "acceptance": [{"id": "AC1", "expected": "e", "environment": "isolated"}],
            }, CAPABILITIES)
            lease = runtime.claim(state["task_id"], "one", ttl_seconds=1)
            heartbeat = runtime.heartbeat(state["task_id"], lease["lease_id"], ttl_seconds=2)
            self.assertEqual("HEARTBEAT", heartbeat["status"])
            with self.assertRaisesRegex(ControlPlaneError, "RECLAIM_REQUIRES_PROCESS_STOP_PROOF"):
                runtime.reclaim(state["task_id"], "two", stop_proof={})
            import time
            time.sleep(2.1)
            reclaimed = runtime.reclaim(state["task_id"], "two",
                                        stop_proof={"process_stopped": True, "source": "host",
                                                    "task_id": state["task_id"],
                                                    "lease_id": lease["lease_id"]})
            self.assertEqual("CLAIMED", reclaimed["status"])
            self.assertEqual("one", reclaimed["reclaimed_from"])

    def test_runtime_budget_blocks_new_actions_but_replays_idempotent_result(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = DeliveryRuntime(Path(temp) / "control.sqlite3")
            contract = self.contract("0" * 64)
            contract["budget"] = {"max_steps": 1, "max_retries": 1}
            state = runtime.create_task(contract, CAPABILITIES)
            lease = runtime.claim(state["task_id"], "runner")
            state, first = runtime.apply(state["task_id"], lease["lease_id"], "start", {},
                                         request_id="start-once")
            replay, second = runtime.apply(state["task_id"], lease["lease_id"], "start", {},
                                           request_id="start-once")
            self.assertEqual(first, second)
            self.assertEqual(replay["control_plane"]["steps_used"], 1)
            with self.assertRaisesRegex(ControlPlaneError, "BUDGET_EXHAUSTED"):
                runtime.apply(state["task_id"], lease["lease_id"], "tool_result",
                              {"tool_call_id": "call-1", "status": "SUCCEEDED"},
                              request_id="new-action")

    def test_independent_task_verification_reports_missing_and_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            content = b"x"
            (evidence_root / "result.txt").write_bytes(content)
            sha = hashlib.sha256(content).hexdigest()
            runtime = DeliveryRuntime(root / "control.sqlite3")
            state = runtime.create_task(self.contract(sha), CAPABILITIES)
            with self.assertRaises(EvaluationError):
                verify_task(state, normalize_contract(self.contract(sha)), evidence_root,
                            reviewer_id="planner")
            result = verify_task(state, normalize_contract(self.contract(sha)), evidence_root,
                                 reviewer_id="reviewer")
            self.assertEqual("INCOMPLETE", result["status"])

    def test_independent_verifier_refuses_blocked_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            content = b"x"
            (evidence_root / "result.txt").write_bytes(content)
            sha = hashlib.sha256(content).hexdigest()
            runtime = DeliveryRuntime(root / "control.sqlite3")
            state = runtime.create_task(self.contract(sha), CAPABILITIES)
            state["status"] = "BLOCKED"
            with self.assertRaisesRegex(EvaluationError, "not independently deliverable"):
                verify_task(state, normalize_contract(self.contract(sha)), evidence_root,
                            reviewer_id="reviewer")

    def test_restart_and_cancel_are_persisted_and_require_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = DeliveryRuntime(Path(temp) / "control.sqlite3")
            state = runtime.create_task({
                "task_id": "restart-task", "goal": "g", "owner": "a", "candidate": "c",
                "authorization_ref": "local", "scope": ["x"], "excluded": ["y"],
                "policy": {"workspace_write": False},
                "acceptance": [{"id": "AC1", "expected": "e", "environment": "isolated"}],
            }, CAPABILITIES)
            lease = runtime.claim(state["task_id"], "runner")
            runtime.apply(state["task_id"], lease["lease_id"], "start", {"executor": "runner"})
            self.assertEqual("RESTARTED", runtime.restart(state["task_id"])["status"])
            with self.assertRaisesRegex(ControlPlaneError, "reason"):
                runtime.cancel(state["task_id"], lease["lease_id"], executor_stopped=True)
            state, response = runtime.cancel(state["task_id"], lease["lease_id"],
                                             executor_stopped=True, reason="host stopped worker")
            self.assertEqual("CANCELLED", state["status"])
            self.assertEqual("CANCELLED", response["status"])


if __name__ == "__main__":
    unittest.main()
