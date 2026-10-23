"""Pure synthetic parsing tests. No Codex/model/authentication calls."""
import json
from pathlib import Path
import tempfile
import unittest

from metrics import aggregate_run


def usage(inp, cached, out, reasoning=0):
    return {"input_tokens": inp, "cached_input_tokens": cached,
            "output_tokens": out, "reasoning_output_tokens": reasoning}


def meta(sid, parent=None):
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "exec"
    return {"type": "session_meta", "payload": {"id": sid, "session_id": parent or sid, "source": source}}


def total(value, at="2026-10-23T00:00:00Z"):
    return {"timestamp": at, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": value}}}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "runtimehome"
        (self.home / "sessions").mkdir(parents=True)
        self.events = self.root / "events.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def run_metrics(self, rows):
        self.write(self.events, rows)
        return aggregate_run([self.events], self.home)

    def test_multiturn_final_cumulative_and_child_not_session_id(self):
        p = self.home / "sessions" / "root.jsonl"
        self.write(p, [meta("main"), total(usage(100, 80, 10, 2)),
            {"type": "turn_context", "payload": {"turn_id": "t2", "model": "gpt-test", "effort": "high"}},
            total(usage(250, 180, 30, 8), "2026-10-23T00:01:00Z")])
        self.write(self.home / "sessions" / "child.jsonl", [meta("child", "main"), total(usage(40, 20, 5, 1))])
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"},
            {"type": "turn.completed", "usage": usage(100, 80, 10, 2)},
            {"type": "turn.completed", "usage": usage(150, 100, 20, 6)}])
        self.assertEqual(result["main_usage"]["input_tokens"], 250)
        self.assertEqual(result["child_usage"]["input_tokens"], 40)
        self.assertEqual(result["observed_total_usage"]["input_tokens"], 290)
        self.assertEqual(result["observed_total_usage"]["uncached_input_tokens"], 90)
        self.assertEqual(result["observed_total_usage"]["total_tokens"], 325)
        self.assertEqual(result["observed_models"], ["gpt-test"])
        self.assertEqual(result["observed_efforts"], ["high"])
        self.assertFalse(result["sessions"][0]["native_terminal_observed"])
        self.assertIsNotNone(result["captured_utc"])

    def test_duplicate_native_files_and_cumulative_records_not_summed(self):
        rows = [meta("main"), total(usage(50, 10, 5)),
            {"timestamp": "2026-10-23T00:00:01Z", "type": "token_usage_record", "payload": {
                "thread_id": "main", "response_id": "r1", "usage": usage(50, 10, 5),
                "thread_token_usage": usage(120, 70, 11, 3)}}]
        self.write(self.home / "sessions" / "a.jsonl", rows)
        self.write(self.home / "sessions" / "b.jsonl", rows)
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"}])
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["main_usage"]["input_tokens"], 120)
        self.assertEqual(result["sessions"][0]["response_records"], 1)

    def test_response_only_dedup_and_missing_reasoning_is_none(self):
        value = {"input_tokens": 5, "cached_input_tokens": 2, "output_tokens": 1}
        row = {"type": "token_usage_record", "payload": {"thread_id": "main", "response_id": "r1", "usage": value}}
        self.write(self.home / "sessions" / "a.jsonl", [meta("main"), row, row])
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"}])
        self.assertEqual(result["main_usage"]["input_tokens"], 5)
        self.assertIsNone(result["main_usage"]["reasoning_output_tokens"])
        self.assertIsNone(result["child_usage"])

    def test_exec_fallback_multiturn_and_identical_stream_copy(self):
        rows = [{"type": "thread.started", "thread_id": "main"},
            {"type": "turn.completed", "turn_id": "t1", "usage": usage(10, 5, 2)},
            {"type": "turn.completed", "turn_id": "t2", "usage": usage(20, 10, 3)}]
        self.write(self.events, rows)
        copy = self.root / "copy.jsonl"
        self.write(copy, rows)
        result = aggregate_run([self.events, copy], self.home)
        self.assertEqual(result["main_usage"]["input_tokens"], 30)
        self.assertEqual(result["exec_completed_usage_events"], 2)
        self.assertIsNone(result["child_usage"])
        self.assertIsNone(result["observed_models"])

    def test_missing_child_record_is_none(self):
        self.write(self.home / "sessions" / "main.jsonl", [meta("main"), total(usage(50, 20, 4))])
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"},
            {"type": "item.completed", "item": {"type": "collab_agent_tool_call", "tool": "spawn_agent", "receiver_thread_ids": ["lost-child"]}}])
        self.assertIsNone(result["child_usage"])
        self.assertEqual(result["child_observability"]["missing_thread_ids"], ["lost-child"])

    def test_malformed_truncated_log_does_not_invent_tokens(self):
        self.events.write_text('{"type":"thread.started","thread_id":"main"}\n{"type":', encoding="utf-8")
        result = aggregate_run([self.events], self.home)
        self.assertIsNone(result["main_usage"])
        self.assertTrue(any(x.startswith("invalid_json:") for x in result["warnings"]))

    def test_latest_timestamp_not_file_order(self):
        self.write(self.home / "sessions" / "a-latest.jsonl", [meta("main"), total(usage(90, 50, 8), "2026-10-23T00:01:00Z")])
        self.write(self.home / "sessions" / "z-old.jsonl", [meta("main"), total(usage(10, 5, 1))])
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"}])
        self.assertEqual(result["main_usage"]["input_tokens"], 90)

    def test_subsets_not_added_and_missing_child_usage_contaminates_total(self):
        self.write(self.home / "sessions" / "main.jsonl", [meta("main"), total(usage(20, 10, 4, 2))])
        self.write(self.home / "sessions" / "child.jsonl", [meta("child", "main")])
        result = self.run_metrics([{"type": "thread.started", "thread_id": "main"}])
        self.assertIsNone(result["child_usage"])
        self.assertIsNone(result["observed_total_usage"]["input_tokens"])
        self.assertEqual(result["main_usage"]["total_tokens"], 24)


if __name__ == "__main__":
    unittest.main()
