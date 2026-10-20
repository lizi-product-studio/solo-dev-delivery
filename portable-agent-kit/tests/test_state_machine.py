import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.state_machine import ProtocolError, apply, digest, start


CAPS = {"fs.read": True, "fs.write": True, "shell.exec": True,
        "evidence.hash": True, "checkpoint.persist": True}


def contract(**policy):
    return {"id": "T-test-001", "goal": "完成一个隔离小改", "owner": "agent/test",
            "authorization_ref": "test-only", "candidate": "candidate-1",
            "scope": ["fixture.txt"], "excluded": ["production", "external send"],
            "acceptance": [{"id": "AC1", "expected": "写入后读回内容一致", "environment": "isolated"}],
            "policy": {"workspace_write": True, "ux_required": False, "external_actions": False, **policy}}


def request(state, op, key, payload=None):
    return {"protocol": "aap/0.1", "request_id": "r-"+key, "op": op,
            "task_id": state["task_id"], "revision": state["revision"],
            "contract_hash": state["contract_hash"], "candidate": state["candidate"],
            "idempotency_key": key, "payload": payload or {}}


class PortableStateMachineTests(unittest.TestCase):
    def test_low_capability_host_blocks_ux_before_model_work(self):
        state = start(contract(ux_required=True), CAPS)
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("browser.control", [x["capability"] for x in state["blockers"]])

    def test_simple_task_requires_evidence_and_readback_before_finish(self):
        state = start(contract(), CAPS)
        state, result = apply(state, request(state, "start", "start", {"executor": "agent/test"}))
        self.assertEqual("RUNNING", result["status"])
        state, result = apply(state, request(state, "finish", "finish-too-early"))
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("NON_PASS_EVIDENCE", [x["code"] for x in state["blockers"]])

    def test_unknown_side_effect_requires_readback_and_does_not_retry(self):
        state = start(contract(external_actions=True), {**CAPS, "external.authorized": True})
        state, _ = apply(state, request(state, "start", "start", {"executor": "agent/test"}))
        state, result = apply(state, request(state, "tool_result", "unknown", {"tool_call_id": "call-1", "status": "UNKNOWN"}))
        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual("READBACK_OR_RECONCILE", result["next_action"])
        state, result = apply(state, request(state, "tool_result", "readback", {"tool_call_id": "call-2", "status": "SUCCEEDED", "observed_state": {"exists": False}}))
        self.assertEqual("RECORD_EVIDENCE", result["next_action"])
        self.assertEqual(["call-1"], state["unknown_tool_calls"])

    def test_pause_resume_requires_checkpoint_and_stopped_executor(self):
        state = start(contract(), CAPS)
        state, _ = apply(state, request(state, "start", "start", {"executor": "agent/test"}))
        with self.assertRaisesRegex(ProtocolError, "CHECKPOINT"):
            apply(state, request(state, "pause", "pause-no-checkpoint", {"executor_stopped": True}))
        state, _ = apply(state, request(state, "checkpoint", "checkpoint", {"id": "cp-1", "cursor": "step-1", "resume_token": "resume-1"}))
        with self.assertRaisesRegex(ProtocolError, "STOP_PROOF"):
            apply(state, request(state, "pause", "pause-no-proof", {"executor_stopped": False}))
        state, result = apply(state, request(state, "pause", "pause", {"executor_stopped": True}))
        self.assertEqual("PAUSED", result["status"])
        state, result = apply(state, request(state, "resume", "resume", {"executor":"agent/test", "resume_token":"resume-1", "executor_verified":True}))
        self.assertEqual("RUNNING", result["status"])

    def test_idempotency_replays_response(self):
        state = start(contract(), CAPS)
        req = request(state, "start", "same", {"executor": "agent/test"})
        state, first = apply(state, req)
        state, second = apply(state, req)
        self.assertEqual(first, second)
        self.assertEqual(1, len(state["events"]))

    def test_evidence_is_bound_to_candidate_and_revision(self):
        state = start(contract(), CAPS)
        state, _ = apply(state, request(state, "start", "start", {"executor": "agent/test"}))
        ref = {"id":"ev-1", "kind":"readback", "uri":"memory://ev-1",
               "sha256": hashlib.sha256(b"evidence").hexdigest(), "claim":"readback matched",
               "verdict":"PASS", "task_id":state["task_id"], "revision":state["revision"],
               "contract_hash":state["contract_hash"], "candidate":state["candidate"]}
        state, result = apply(state, request(state, "evidence", "evidence", ref))
        self.assertEqual("CONTINUE", result["next_action"])
        ref["candidate"] = "old-candidate"
        with self.assertRaisesRegex(ProtocolError, "candidate mismatch"):
            apply(state, request(state, "evidence", "bad-evidence", ref))


if __name__ == "__main__":
    unittest.main()
