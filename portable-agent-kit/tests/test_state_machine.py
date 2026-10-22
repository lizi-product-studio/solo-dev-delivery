import hashlib
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.state_machine import ProtocolError, apply, start


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


def evidence(state, evidence_id="ev-1", check_id="AC1", **changes):
    return {"id": evidence_id, "kind": "local-check", "uri": "fixture.txt",
            "sha256": hashlib.sha256(b"actual content").hexdigest(), "claim": "readback matched",
            "verdict": "PASS", "task_id": state["task_id"], "revision": state["revision"],
            "contract_hash": state["contract_hash"], "candidate": state["candidate"],
            "check_id": check_id, "environment": "isolated", **changes}


def two_check_contract():
    value = contract()
    value["acceptance"].append({"id": "AC2", "expected": "第二项结果正确", "environment": "browser"})
    return value


class PortableStateMachineTests(unittest.TestCase):
    def running(self, task_contract=None, capabilities=None, executor=None):
        state = start(task_contract or contract(), CAPS if capabilities is None else capabilities)
        state, _ = apply(state, request(state, "start", "start", {"executor": executor}))
        return state

    def record(self, state, ref=None, verifier=lambda _: True):
        ref = ref or evidence(state)
        return apply(state, request(state, "evidence", ref["id"], ref), verify_evidence=verifier)

    def finish(self, state, key="finish", verifier=lambda _: True, **payload):
        return apply(state, request(state, "finish", key, payload), verify_evidence=verifier)

    def codes(self, state):
        return {x["code"] for x in state["blockers"]}

    def test_low_capability_host_blocks_ux_before_model_work(self):
        state = start(contract(ux_required=True), CAPS)
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("browser.control", [x["capability"] for x in state["blockers"]])
        with self.assertRaisesRegex(ProtocolError, "not ready"):
            apply(state, request(state, "start", "start"))

    def test_explicit_required_capabilities_are_merged_and_unknown_is_blocking(self):
        value = contract(workspace_write=False)
        value["required_capabilities"] = ["fs.read", "semantic.review"]
        state = start(value, {"evidence.hash": True, "checkpoint.persist": True, "fs.read": True})
        self.assertEqual(["checkpoint.persist", "evidence.hash", "fs.read", "semantic.review"], state["required_capabilities"])
        self.assertEqual("BLOCKED", state["status"])
        self.assertFalse(next(b for b in state["blockers"] if b["capability"] == "semantic.review")["known"])
        with self.assertRaisesRegex(ProtocolError, "required_capabilities"):
            start({**value, "required_capabilities": [""]}, CAPS)

    def test_contract_acceptance_and_policy_are_retained_without_aliasing(self):
        value = contract()
        state = start(value, CAPS)
        self.assertEqual(value, state["contract"])
        self.assertEqual(value["acceptance"], state["acceptance"])
        self.assertEqual(value["policy"], state["policy"])
        value["acceptance"][0]["expected"] = "changed by caller"
        self.assertNotEqual(value["acceptance"], state["acceptance"])

    def test_finish_requires_start_and_does_not_trap_ready_state(self):
        state = start(contract(), CAPS)
        state, _ = self.record(state)
        with self.assertRaisesRegex(ProtocolError, "NOT_STARTED"):
            self.finish(state)
        self.assertEqual("READY", state["status"])
        state, _ = apply(state, request(state, "start", "start"))
        state, _ = self.finish(state)
        self.assertEqual("COMPLETED", state["status"])

    def test_omitted_acceptance_blocks_and_correct_evidence_recovers_finish(self):
        state = self.running(two_check_contract())
        state, _ = self.record(state)
        state, response = self.finish(state, "finish-missing")
        self.assertEqual("BLOCKED", response["status"])
        self.assertEqual(["AC2"], state["uncovered"])
        self.assertIn("UNCOVERED_ACCEPTANCE", self.codes(state))
        state, _ = self.record(state, evidence(state, "ev-2", "AC2", environment="browser"))
        self.assertEqual("RUNNING", state["status"])
        state, response = self.finish(state)
        self.assertEqual("COMPLETED", response["status"])
        self.assertEqual([], state["uncovered"])

    def test_no_evidence_blocks_completion(self):
        state, response = self.finish(self.running())
        self.assertEqual("BLOCKED", response["status"])
        self.assertIn("NON_PASS_EVIDENCE", self.codes(state))
        self.assertEqual(["AC1"], state["uncovered"])

    def test_forged_verified_flags_and_pass_cannot_authenticate_evidence(self):
        state = self.running()
        ref = evidence(state, verified=True, host_verified=True, verification="verified")
        state, _ = self.record(state, ref, verifier=None)
        self.assertFalse(state["evidence"][0]["host_verified"])
        self.assertEqual("unverified", state["evidence"][0]["verification"])
        self.assertNotIn("verified", state["evidence"][0])
        state, _ = self.finish(state, verifier=None)
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("UNVERIFIED_EVIDENCE", self.codes(state))

    def test_false_non_boolean_and_throwing_verifiers_fail_closed(self):
        def throwing(_):
            raise RuntimeError("host probe unavailable")
        for index, verifier in enumerate((lambda _: False, lambda _: "PASS", lambda _: 1, throwing)):
            with self.subTest(index=index):
                state, _ = self.record(self.running(), verifier=verifier)
                state, _ = self.finish(state, verifier=verifier)
                self.assertEqual("BLOCKED", state["status"])
                self.assertFalse(state["evidence"][0]["host_verified"])

    def test_evidence_hash_must_be_hex_and_reference_bound_to_current_task(self):
        state = self.running()
        for key, bad in (("sha256", "z"*64), ("sha256", "0"*63), ("task_id", "other"),
                         ("revision", 0), ("contract_hash", "old-contract"), ("candidate", "old-candidate")):
            with self.subTest(key=key, bad=bad), self.assertRaises(ProtocolError):
                self.record(state, evidence(state, **{key: bad}))
        with self.assertRaisesRegex(ProtocolError, "unknown evidence check_id"):
            self.record(state, evidence(state, check_id="NOT-AC"))

    def test_wrong_environment_and_legacy_missing_check_id_do_not_cover(self):
        for mode in ("environment", "legacy"):
            with self.subTest(mode=mode):
                state = self.running()
                ref = evidence(state)
                if mode == "environment":
                    ref["environment"] = "production"
                else:
                    ref.pop("check_id")
                calls = []
                state, _ = self.record(state, ref, verifier=lambda e: calls.append(e) or True)
                state, _ = self.finish(state)
                self.assertEqual("BLOCKED", state["status"])
                self.assertEqual(["AC1"], state["uncovered"])
                self.assertFalse(state["evidence"][0]["host_verified"])
                self.assertEqual([], calls)

    def test_finish_revalidates_all_current_evidence_and_detects_artifact_change(self):
        state = self.running(two_check_contract())
        state, _ = self.record(state)
        state, _ = self.record(state, evidence(state, "ev-2", "AC2", environment="browser"))
        inspected = []
        def verifier(ref):
            inspected.append(ref["id"])
            return ref["id"] != "ev-1"
        state, _ = self.finish(state, "changed-artifact", verifier=verifier)
        self.assertCountEqual(["ev-1", "ev-2"], inspected)
        self.assertEqual("BLOCKED", state["status"])
        self.assertEqual(["AC1"], state["uncovered"])
        self.assertFalse(state["evidence"][0]["host_verified"])
        # A previously authentic proof cannot be reused when no host verifier runs.
        state, _ = self.finish(state, "no-verifier", verifier=None)
        self.assertTrue(all(not e["host_verified"] for e in state["evidence"]))

    def test_host_verifier_receives_a_copy_without_claimed_verification_flags(self):
        state = self.running()
        def verifier(ref):
            self.assertNotIn("host_verified", ref)
            self.assertNotIn("verified", ref)
            ref["claim"] = "callback mutation"
            return True
        state, _ = self.record(state, evidence(state, host_verified=True), verifier=verifier)
        self.assertEqual("readback matched", state["evidence"][0]["claim"])

    def test_fix_replaces_only_same_check_and_preserves_failed_history(self):
        state = self.running(two_check_contract())
        state, _ = self.record(state, evidence(state, "failed", verdict="FAIL"))
        state, _ = self.record(state, evidence(state, "unrelated", "AC2", environment="browser"))
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("NON_PASS_EVIDENCE", self.codes(state))
        state, _ = self.record(state, evidence(state, "fixed"))
        state, _ = self.finish(state)
        self.assertEqual("COMPLETED", state["status"])
        self.assertEqual("FAIL", state["evidence"][0]["verdict"])
        self.assertEqual("fixed", state["evidence"][0]["superseded_by"])
        self.assertEqual("failed", state["evidence"][2]["supersedes"])
        self.assertEqual({"AC1": "fixed", "AC2": "unrelated"}, state["current_evidence"])
        self.assertEqual(3, len(state["evidence"]))

    def test_duplicate_evidence_id_cannot_rewrite_history(self):
        state, _ = self.record(self.running())
        with self.assertRaisesRegex(ProtocolError, "duplicate evidence id"):
            apply(state, request(state, "evidence", "reuse-id", evidence(state, verdict="FAIL")), verify_evidence=lambda _: True)
        self.assertEqual("PASS", state["evidence"][0]["verdict"])

    def test_pass_does_not_remove_capability_or_host_blockers(self):
        state = self.running()
        state["capabilities"]["evidence.hash"] = "unsupported"
        state["blockers"].append({"code": "HOST_REVIEW_REQUIRED"})
        state, _ = self.record(state)
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("CAPABILITY_REQUIRED", self.codes(state))
        self.assertIn("HOST_REVIEW_REQUIRED", self.codes(state))
        state, _ = self.finish(state)
        self.assertEqual("BLOCKED", state["status"])

    def test_unknown_side_effect_cannot_be_cleared_by_pass_or_another_tool_result(self):
        state = self.running()
        state, _ = apply(state, request(state, "tool_result", "unknown", {"tool_call_id": "call-1", "status": "UNKNOWN"}))
        state, _ = apply(state, request(state, "tool_result", "success", {"tool_call_id": "call-1", "status": "SUCCEEDED"}))
        state, _ = self.record(state)
        self.assertEqual("UNKNOWN", state["status"])
        self.assertEqual(["call-1"], state["unknown_tool_calls"])
        state, _ = self.finish(state)
        self.assertEqual("UNKNOWN", state["status"])
        self.assertIn("UNKNOWN_SIDE_EFFECT", self.codes(state))

    def test_reconcile_requires_current_verified_bound_readback_and_reverifies(self):
        state = self.running()
        state, _ = apply(state, request(state, "tool_result", "unknown", {"tool_call_id": "call-1", "status": "UNKNOWN"}))
        state, _ = self.record(state)
        reconcile_payload = {"tool_call_id": "call-1", "evidence_id": "ev-1", "observed_outcome": "SUCCEEDED"}
        with self.assertRaisesRegex(ProtocolError, "READBACK_EVIDENCE"):
            apply(state, request(state, "reconcile", "bad-kind", reconcile_payload), verify_evidence=lambda _: True)
        ref = evidence(state, "readback", kind="readback", tool_call_id="call-1", observed_outcome="SUCCEEDED")
        state, _ = self.record(state, ref)
        reconcile_payload["evidence_id"] = "readback"
        with self.assertRaisesRegex(ProtocolError, "OUTCOME_MISMATCH"):
            apply(state, request(state, "reconcile", "wrong-outcome", {**reconcile_payload, "observed_outcome": "FAILED"}), verify_evidence=lambda _: True)
        with self.assertRaisesRegex(ProtocolError, "NOT_VERIFIED"):
            apply(state, request(state, "reconcile", "unverified", reconcile_payload), verify_evidence=lambda _: False)
        self.assertEqual(["call-1"], state["unknown_tool_calls"])
        state, _ = apply(state, request(state, "reconcile", "reconcile", reconcile_payload), verify_evidence=lambda _: True)
        self.assertEqual([], state["unknown_tool_calls"])
        self.assertEqual("RUNNING", state["status"])
        state, _ = self.finish(state)
        self.assertEqual("COMPLETED", state["status"])

    def test_reconcile_cannot_use_readback_that_predates_unknown(self):
        state = self.running()
        state, _ = self.record(state, evidence(state, kind="readback", tool_call_id="call-1", observed_outcome="SUCCEEDED"))
        state, _ = apply(state, request(state, "tool_result", "unknown", {"tool_call_id": "call-1", "status": "UNKNOWN"}))
        with self.assertRaisesRegex(ProtocolError, "FOLLOW_UNKNOWN"):
            apply(state, request(state, "reconcile", "reconcile", {"tool_call_id": "call-1", "evidence_id": "ev-1", "observed_outcome": "SUCCEEDED"}), verify_evidence=lambda _: True)

    def test_reconcile_failed_outcome_keeps_failure_and_other_unknown_blocking(self):
        state = self.running()
        for call in ("call-1", "call-2"):
            state, _ = apply(state, request(state, "tool_result", "unknown-"+call, {"tool_call_id": call, "status": "UNKNOWN"}))
        state, _ = self.record(state, evidence(state, kind="readback", tool_call_id="call-1", observed_outcome="FAILED"))
        state, _ = apply(state, request(state, "reconcile", "reconcile", {"tool_call_id": "call-1", "evidence_id": "ev-1", "observed_outcome": "FAILED"}), verify_evidence=lambda _: True)
        self.assertEqual(["call-2"], state["unknown_tool_calls"])
        self.assertEqual(["call-1"], state["failed_tool_calls"])
        self.assertIn("TOOL_FAILED", self.codes(state))
        self.assertEqual("UNKNOWN", state["status"])

    def test_tool_failure_blocks_until_explicit_success_of_same_known_call(self):
        state = self.running()
        state, _ = apply(state, request(state, "tool_result", "failed", {"tool_call_id": "call-1", "status": "FAILED"}))
        state, _ = self.record(state)
        self.assertIn("TOOL_FAILED", self.codes(state))
        state, _ = apply(state, request(state, "tool_result", "unrelated", {"tool_call_id": "call-2", "status": "SUCCEEDED"}))
        self.assertEqual("BLOCKED", state["status"])
        state, _ = apply(state, request(state, "tool_result", "retry", {"tool_call_id": "call-1", "status": "SUCCEEDED"}))
        state, _ = self.finish(state)
        self.assertEqual("COMPLETED", state["status"])

    def test_findings_default_to_all_checks_and_reject_non_acceptance_ids(self):
        state = self.running(two_check_contract())
        with self.assertRaisesRegex(ProtocolError, "check_ids"):
            apply(state, request(state, "open_finding", "bad-finding", {"id": "F1", "check_ids": ["other"]}))
        state, _ = apply(state, request(state, "open_finding", "finding", {"id": "F1"}))
        self.assertEqual(["AC1", "AC2"], state["findings"][0]["check_ids"])
        with self.assertRaisesRegex(ProtocolError, "duplicate finding"):
            apply(state, request(state, "open_finding", "duplicate-finding", {"id": "F1"}))

    def test_finding_needs_current_post_finding_verified_retests_for_every_check(self):
        state = self.running(two_check_contract())
        state, _ = self.record(state, evidence(state, "before-finding"))
        state, _ = apply(state, request(state, "open_finding", "finding", {"id": "F1"}))
        with self.assertRaisesRegex(ProtocolError, "REQUIRES_RETEST"):
            apply(state, request(state, "resolve_finding", "old-proof", {"id": "F1", "evidence_ids": ["before-finding"]}), verify_evidence=lambda _: True)
        state, _ = self.record(state, evidence(state, "fix-1"))
        with self.assertRaisesRegex(ProtocolError, "CHECKS_UNCOVERED"):
            apply(state, request(state, "resolve_finding", "partial", {"id": "F1", "evidence_ids": ["fix-1"]}), verify_evidence=lambda _: True)
        state, _ = self.record(state, evidence(state, "fix-2", "AC2", environment="browser"))
        payload = {"id": "F1", "evidence_ids": ["fix-1", "fix-2"]}
        with self.assertRaisesRegex(ProtocolError, "NOT_VERIFIED"):
            apply(state, request(state, "resolve_finding", "changed", payload), verify_evidence=lambda _: False)
        self.assertEqual(["F1"], state["open_findings"])
        state, _ = self.finish(state, "still-finding")
        self.assertIn("OPEN_FINDINGS", self.codes(state))
        state, _ = apply(state, request(state, "resolve_finding", "resolved", payload), verify_evidence=lambda _: True)
        self.assertEqual([], state["open_findings"])
        self.assertEqual("RESOLVED", state["findings"][0]["status"])
        state, _ = self.finish(state)
        self.assertEqual("COMPLETED", state["status"])

    def test_finding_cannot_resolve_with_superseded_evidence(self):
        state = self.running()
        state, _ = apply(state, request(state, "open_finding", "finding", {"id": "F1", "check_ids": ["AC1"]}))
        state, _ = self.record(state, evidence(state, "passed"))
        state, _ = self.record(state, evidence(state, "failed", verdict="FAIL"))
        with self.assertRaisesRegex(ProtocolError, "CURRENT_EVIDENCE"):
            apply(state, request(state, "resolve_finding", "resolve", {"id": "F1", "evidence_ids": ["passed"]}), verify_evidence=lambda _: True)

    def test_pause_resume_preserves_unknown_and_requires_stop_checkpoint_and_owner(self):
        state = self.running(executor="agent/test")
        with self.assertRaisesRegex(ProtocolError, "CHECKPOINT"):
            apply(state, request(state, "pause", "no-checkpoint", {"executor_stopped": True}))
        state, _ = apply(state, request(state, "checkpoint", "checkpoint", {"id": "cp-1", "cursor": "step-1", "resume_token": "resume-1"}))
        with self.assertRaisesRegex(ProtocolError, "STOP_PROOF"):
            apply(state, request(state, "pause", "no-proof", {"executor_stopped": False}))
        state, _ = apply(state, request(state, "tool_result", "unknown", {"tool_call_id": "call-1", "status": "UNKNOWN"}))
        state, _ = apply(state, request(state, "pause", "pause", {"executor_stopped": True}))
        state, _ = self.record(state)
        state, _ = self.finish(state, "paused-finish")
        self.assertEqual("PAUSED", state["status"])
        with self.assertRaisesRegex(ProtocolError, "OWNER_VERIFICATION"):
            apply(state, request(state, "resume", "unverified-resume", {"resume_token": "resume-1"}))
        state, _ = apply(state, request(state, "resume", "resume", {"executor": "agent/test", "resume_token": "resume-1", "executor_verified": True}))
        self.assertEqual("UNKNOWN", state["status"])
        self.assertEqual(["call-1"], state["unknown_tool_calls"])

    def test_active_executor_blocker_persists_across_evidence_until_stop_proof(self):
        state = self.running(executor="agent/test")
        state, _ = self.record(state)
        state, _ = self.finish(state, "still-active")
        self.assertIn("ACTIVE_EXECUTOR", self.codes(state))
        state, _ = self.record(state, evidence(state, "new-pass"))
        self.assertIn("ACTIVE_EXECUTOR", self.codes(state))
        state, _ = self.finish(state, executor_stopped=True)
        self.assertEqual("COMPLETED", state["status"])
        self.assertIsNone(state["active_executor"])

    def test_cancel_requires_explicit_stop_proof_for_active_executor(self):
        state = self.running(executor="agent/test")
        with self.assertRaisesRegex(ProtocolError, "EXPLICIT_REQUEST"):
            apply(state, request(state, "cancel", "implicit"))
        with self.assertRaisesRegex(ProtocolError, "STOP_PROOF"):
            apply(state, request(state, "cancel", "still-active", {"explicit": True}))
        self.assertEqual("agent/test", state["active_executor"])
        state, _ = apply(state, request(state, "cancel", "cancel", {"explicit": True, "executor_stopped": True}))
        self.assertEqual("CANCELLED", state["status"])

    def test_observe_threshold_requests_decision_without_killing_executor(self):
        state = self.running(executor="agent/test")
        state, response = apply(state, request(state, "observe", "threshold", {"checkpoint_due": True, "elapsed_seconds": 999999}))
        self.assertEqual("DECIDE_CONTINUE_OR_PAUSE", response["next_action"])
        self.assertEqual("agent/test", state["active_executor"])
        self.assertEqual("RUNNING", state["status"])

    def test_idempotency_replays_exact_request_and_rejects_key_reuse(self):
        state = start(contract(), CAPS)
        req = request(state, "start", "same", {"executor": "agent/test"})
        state, first = apply(state, req)
        state, second = apply(state, req)
        self.assertEqual(first, second)
        self.assertEqual(1, len(state["events"]))
        for replacement in ({**req, "op": "observe"}, {**req, "payload": {"executor": "other"}}, {**req, "request_id": "different"}):
            with self.assertRaisesRegex(ProtocolError, "IDEMPOTENCY_KEY_CONFLICT"):
                apply(state, replacement)

    def test_terminal_lock_rejects_all_mutations_but_allows_known_replay(self):
        state = self.running()
        state, _ = self.record(state)
        final_request = request(state, "finish", "finish")
        state, final_response = apply(state, final_request, verify_evidence=lambda _: True)
        snapshot = deepcopy(state)
        state, replay = apply(state, final_request, verify_evidence=lambda _: False)
        self.assertEqual(final_response, replay)
        self.assertEqual(snapshot, state)
        for op in ("start", "observe", "tool_result", "evidence", "open_finding", "resolve_finding", "reconcile",
                   "checkpoint", "pause", "resume", "phase", "finish", "cancel"):
            with self.subTest(op=op), self.assertRaisesRegex(ProtocolError, "TERMINAL_STATE_LOCKED"):
                apply(state, request(state, op, "late-"+op))
        state["status"] = "FAILED"
        with self.assertRaisesRegex(ProtocolError, "TERMINAL_STATE_LOCKED"):
            apply(state, request(state, "phase", "late-failed", {"phase": "work"}))

    def test_cancelled_terminal_is_locked(self):
        state = self.running()
        state, _ = apply(state, request(state, "cancel", "cancel", {"explicit": True}))
        with self.assertRaisesRegex(ProtocolError, "TERMINAL_STATE_LOCKED"):
            self.record(state)

    def test_old_state_requires_explicit_host_upgrade_without_mutation(self):
        state = self.running()
        self.assertEqual(2, state["state_version"])
        for old_version in (None, 1):
            old = deepcopy(state)
            if old_version is None:
                old.pop("state_version")
            else:
                old["state_version"] = old_version
            old["evidence"].append({"verdict": "PASS", "verified": True})
            snapshot = deepcopy(old)
            with self.subTest(version=old_version), self.assertRaisesRegex(ProtocolError, "STATE_UPGRADE_REQUIRED"):
                apply(old, request(old, "finish", "old-finish"), verify_evidence=lambda _: True)
            self.assertEqual(snapshot, old)

    def test_producer_is_assigned_by_host_and_numeric_capability_is_unknown(self):
        state = self.running()
        state, _ = self.record(state, evidence(state, producer="forged-host"))
        self.assertEqual("unknown-adapter", state["evidence"][0]["producer"])
        state = start(contract(), {**CAPS, "evidence.hash": {"supported": 1}})
        self.assertEqual("unknown", state["capabilities"]["evidence.hash"])
        self.assertEqual("BLOCKED", state["status"])

    def test_stale_headers_reject_without_mutating_state(self):
        state = self.running()
        saved = deepcopy(state)
        for key, bad in (("revision", 2), ("candidate", "old"), ("contract_hash", "old")):
            with self.subTest(key=key), self.assertRaisesRegex(ProtocolError, "STALE"):
                apply(state, {**request(state, "observe", key), key: bad})
        self.assertEqual(saved, state)


if __name__ == "__main__":
    unittest.main()
