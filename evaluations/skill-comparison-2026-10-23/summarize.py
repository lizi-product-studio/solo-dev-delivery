"""Read-only trial reporting. No model, network, home/config or evaluator access.

Run without arguments to write summary.json and summary.md beside this script.
--brief prints a compact progress snapshot without writing reports.
--self-test validates aggregation on in-memory, synthetic receipts only.

Optional blind-review-results.json schema (no prose/semantic inference):
  {"reviews": [{"run_id": "...", "first_turn_business_pass": true,
                 "after_protocol_business_pass": false, "note": "..."}]}
The reviews value may alternatively be an object keyed by exact run ID. The two
boolean fields are optional; absent/null values stay unknown. Raw review entries
are preserved independently of automatic checker results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any


TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens",
                "output_tokens", "reasoning_output_tokens", "total_tokens")
NATIVE_SOURCES = {"native_last_thread_cumulative", "native_deduplicated_response_usage"}
ERROR_STATUSES = {"execution_error_unclassified", "infra_error", "harness_error"}
REVIEW_FIELDS = ("first_turn_business_pass", "after_protocol_business_pass")


def number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def stats(values: list[Any]) -> dict:
    """Full statistics stay unknown if any sample is unknown; partials are named."""
    known = [v for value in values if (v := number(value)) is not None]
    complete = bool(values) and len(known) == len(values)
    observed = {"sum": sum(known) if known else None,
                "mean": statistics.mean(known) if known else None,
                "median": statistics.median(known) if known else None}
    return {"n": len(values), "n_known": len(known), "n_unknown": len(values) - len(known),
            **{key: value if complete else None for key, value in observed.items()},
            **{f"observed_{key}": value for key, value in observed.items()}}


def boolean_counts(values: list[Any]) -> dict:
    passed = sum(v is True for v in values)
    failed = sum(v is False for v in values)
    return {"n": len(values), "pass": passed, "fail": failed,
            "unknown": len(values) - passed - failed,
            "observed_pass_rate": passed / (passed + failed) if passed + failed else None}


def usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {key: number(value.get(key)) for key in TOKEN_FIELDS}
    # Cache and reasoning are subsets; total is input + output, never all fields.
    inp, out, cached = result["input_tokens"], result["output_tokens"], result["cached_input_tokens"]
    result["total_tokens"] = inp + out if inp is not None and out is not None else None
    result["uncached_input_tokens"] = inp - cached if inp is not None and cached is not None and cached <= inp else None
    return result if any(v is not None for v in result.values()) else None


def sum_usage(values: list[dict | None]) -> dict | None:
    if not values or all(value is None for value in values):
        return None
    return {key: sum(value[key] for value in values)
            if all(value is not None and value.get(key) is not None for value in values) else None
            for key in TOKEN_FIELDS}


def native_usage(metrics: dict) -> dict:
    sessions = metrics.get("sessions")
    sessions = [s for s in sessions if isinstance(s, dict)] if isinstance(sessions, list) else []
    linked = [s for s in sessions if s.get("role") in ("main", "child")]
    main = [s for s in linked if s.get("role") == "main"]
    children = [s for s in linked if s.get("role") == "child"]

    def native_total(rows):
        # A fallback or missing session cannot become zero through filtering.
        return sum_usage([usage(s.get("usage")) if s.get("usage_source") in NATIVE_SOURCES else None for s in rows])

    observation = metrics.get("child_observability")
    observation = observation if isinstance(observation, dict) else {}
    main_total = native_total(main)
    observed_children = native_total(children)
    # metrics.py deliberately leaves absent/unresolved child observations unknown.
    child_total = observed_children if usage(metrics.get("child_usage")) is not None else None
    combined = sum_usage([main_total, child_total]) if main_total is not None and child_total is not None else None
    return {"main_usage": main_total, "child_usage": child_total,
            "main_plus_child_usage": combined,
            "observed_child_usage": observed_children,
            "observed_main_plus_child_usage": native_total(linked),
            "observed_main_threads": len(main), "observed_child_threads": len(children),
            "non_native_or_missing_usage_threads": sum(s.get("usage_source") not in NATIVE_SOURCES or usage(s.get("usage")) is None for s in linked),
            "unlinked_threads": sum(s.get("role") == "unlinked" for s in sessions),
            "child_observability": observation}


def expected_samples(registration: dict) -> list[dict]:
    result, seen = [], set()
    for block in registration.get("blocks", []):
        if not isinstance(block, dict):
            continue
        for arm in block.get("arms", []):
            task, repeat = block.get("task"), block.get("repeat")
            run_id = f"{task}-r{repeat}-{arm}"
            if run_id in seen:
                raise ValueError(f"Duplicate registered sample: {run_id}")
            seen.add(run_id)
            result.append({"id": run_id, "task": task, "repeat": repeat, "arm": arm})
    if not result:
        raise ValueError("registration.blocks contains no samples")
    return result


def parse_reviews(value: Any, warnings: list[str]) -> dict[str, dict]:
    if value is None:
        return {}
    entries = value.get("reviews") if isinstance(value, dict) and "reviews" in value else value
    if isinstance(entries, dict):
        entries = [{**entry, "run_id": key} for key, entry in entries.items() if isinstance(entry, dict)]
    if not isinstance(entries, list):
        warnings.append("blind_review_schema_unrecognized")
        return {}
    output = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("run_id"), str):
            warnings.append("blind_review_entry_missing_exact_run_id")
            continue
        key = entry["run_id"]
        if key in output:
            warnings.append(f"duplicate_blind_review:{key}")
            # Ambiguous duplicate review cannot supply a business verdict.
            output[key] = {"run_id": key, "duplicate_entries": True,
                           **{field: None for field in REVIEW_FIELDS}}
        else:
            output[key] = entry
    return output


def sample(expected: dict, receipt: dict | None, review: dict | None, read_error: str | None = None) -> dict:
    row = {**expected, "receipt_state": "unreadable" if read_error else "pending",
           "receipt_status": None, "started_utc": None, "ended_utc": None,
           "first_turn_automatic_business_pass": None, "after_protocol_automatic_business_pass": None,
           "turns_observed": None, "execution_seconds": None,
           "synthetic_user_replies": None, "real_user_interventions": None,
           "budget_censored": None, "execution_error": None,
           "turns_with_errors": None, "error_event_count": None,
           "error_details": [], "metrics_error": None, "metrics_warnings": [],
           "native_usage": native_usage({}), "reported_usage": None,
           "observed_models": None, "observed_efforts": None,
           "blind_review": {"present": review is not None, "raw": review,
                            **{field: boolean(review.get(field)) if review else None for field in REVIEW_FIELDS}},
           "warnings": [read_error] if read_error else []}
    if receipt is None:
        return row
    for key in ("id", "task", "arm", "repeat"):
        if receipt.get(key) != expected[key]:
            row["warnings"].append(f"receipt_identity_mismatch:{key}")
    finished = bool(receipt.get("ended_utc"))
    turns = receipt.get("turns", [])
    turns = [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else []
    first = next((t for t in turns if t.get("number") == 1), None)
    last = turns[-1] if turns else None
    result = (receipt.get("submission") or {}).get("result", {}) if isinstance(receipt.get("submission"), dict) else {}
    result = result if isinstance(result, dict) else {}
    final_pass = boolean(result.get("completed"))
    if final_pass is None and last:
        final_pass = boolean(last.get("business_completed"))
    budget = receipt.get("status") == "budget_censored" or any(t.get("budget_censored") is True for t in turns)
    erroneous = []
    for turn in turns:
        events = turn.get("errors")
        events = events if isinstance(events, list) else []
        nonzero = isinstance(turn.get("returncode"), int) and turn["returncode"] != 0 and not turn.get("budget_censored")
        if events or nonzero or (turn.get("turn_completed") is False and not turn.get("budget_censored")):
            erroneous.append({"turn": turn.get("number"), "returncode": turn.get("returncode"),
                              "turn_completed": boolean(turn.get("turn_completed")),
                              "error_event_types": [e.get("type") if isinstance(e, dict) else None for e in events]})
    error = receipt.get("status") in ERROR_STATUSES or bool(erroneous)
    metrics = receipt.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    row.update(receipt_state="finished" if finished else "running", receipt_status=receipt.get("status"),
               started_utc=receipt.get("started_utc"), ended_utc=receipt.get("ended_utc"),
               first_turn_automatic_business_pass=boolean(first.get("business_completed")) if first else None,
               after_protocol_automatic_business_pass=final_pass if finished else None,
               turns_observed=len(turns), execution_seconds=number(receipt.get("execution_seconds")),
               synthetic_user_replies=number(receipt.get("synthetic_user_replies")),
               real_user_interventions=number(receipt.get("real_user_interventions")),
               budget_censored=True if budget else False if finished else None,
               execution_error=True if error else False if finished else None,
               turns_with_errors=len(erroneous),
               error_event_count=sum(len(t.get("errors", [])) for t in turns if isinstance(t.get("errors", []), list)),
               error_details=erroneous, metrics_error=receipt.get("metrics_error"),
               metrics_warnings=metrics.get("warnings", []), native_usage=native_usage(metrics),
               reported_usage={key: usage(metrics.get(key)) for key in ("main_usage", "child_usage", "observed_child_usage", "observed_total_usage")},
               observed_models=metrics.get("observed_models"), observed_efforts=metrics.get("observed_efforts"))
    if receipt.get("error"):
        row["error_details"].append({"receipt_error": receipt["error"]})
    return row


def aggregate(rows: list[dict]) -> dict:
    usage_keys = ("main_usage", "child_usage", "main_plus_child_usage", "observed_child_usage", "observed_main_plus_child_usage")
    return {"samples": len(rows), "receipt_states": dict(Counter(r["receipt_state"] for r in rows)),
            "receipt_statuses": dict(Counter(r["receipt_status"] for r in rows if r["receipt_status"] is not None)),
            "first_turn_automatic_business_pass": boolean_counts([r["first_turn_automatic_business_pass"] for r in rows]),
            "after_protocol_automatic_business_pass": boolean_counts([r["after_protocol_automatic_business_pass"] for r in rows]),
            "budget_censored": boolean_counts([r["budget_censored"] for r in rows]),
            "execution_error": boolean_counts([r["execution_error"] for r in rows]),
            **{key: stats([r[key] for r in rows]) for key in ("execution_seconds", "synthetic_user_replies", "real_user_interventions", "turns_with_errors", "error_event_count")},
            "metrics_error_samples": sum(r["metrics_error"] is not None for r in rows),
            "native_usage": {key: {field: stats([(r["native_usage"].get(key) or {}).get(field) for r in rows]) for field in TOKEN_FIELDS} for key in usage_keys},
            "native_usage_coverage": {"child_coverage": dict(Counter(r["native_usage"]["child_observability"].get("coverage", "unavailable") for r in rows)),
                                      "samples_with_native_main": sum(r["native_usage"]["main_usage"] is not None for r in rows),
                                      "samples_with_native_child": sum(r["native_usage"]["child_usage"] is not None for r in rows),
                                      "samples_with_non_native_or_missing_linked_threads": sum(r["native_usage"]["non_native_or_missing_usage_threads"] > 0 for r in rows)},
            "blind_review": {"entries": sum(r["blind_review"]["present"] for r in rows),
                            **{field: boolean_counts([r["blind_review"][field] for r in rows]) for field in REVIEW_FIELDS}}}


def build_summary(registration: dict, receipts: dict[str, dict], reviews_value: Any = None,
                  read_errors: dict[str, str] | None = None) -> dict:
    warnings: list[str] = []
    reviews = parse_reviews(reviews_value, warnings)
    expected = expected_samples(registration)
    expected_ids = {e["id"] for e in expected}
    for key in sorted(set(receipts) - expected_ids):
        warnings.append(f"unregistered_receipt_excluded:{key}")
    for key in sorted(set(reviews) - expected_ids):
        warnings.append(f"unregistered_blind_review_excluded:{key}")
    rows = [sample(e, receipts.get(e["id"]), reviews.get(e["id"]), (read_errors or {}).get(e["id"])) for e in expected]
    states = Counter(r["receipt_state"] for r in rows)
    arms = list(dict.fromkeys(registration.get("arms", []) + [r["arm"] for r in rows]))
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    return {"schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
            "status": "complete" if states["finished"] == len(rows) else "running",
            "expected_samples": len(rows), "finished_samples": states["finished"],
            "running_samples": states["running"], "pending_samples": states["pending"],
            "unreadable_samples": states["unreadable"],
            "registration": {key: registration.get(key) for key in ("registered_utc", "exploratory", "seed", "arms", "model", "effort", "tier", "max_seconds_per_case", "max_turns", "concurrent_cases", "limits", "handoff_semantics")},
            "blind_review_status": "absent" if reviews_value is None else "loaded",
            "groups": {arm: aggregate([r for r in rows if r["arm"] == arm]) for arm in arms},
            "tasks": {task: {arm: aggregate([r for r in rows if r["task"] == task and r["arm"] == arm]) for arm in arms} for task in tasks},
            "samples": rows, "warnings": warnings,
            "interpretation": [
                "Automatic checker outcomes and explicit blind-review verdicts are separate; no semantic verdict is inferred from prose.",
                "After-protocol automatic outcome is unknown until the receipt has ended_utc; first-turn checks already observed may be shown while running.",
                "sum/mean/median require every registered sample to be known; observed_* describe known values only and report n_known/n_unknown.",
                "Native usage accepts only native_last_thread_cumulative or native_deduplicated_response_usage; exec fallback is retained under per-sample reported_usage, not labelled native.",
                "main_plus_child_usage requires measured native main and child usage. An absent child is unknown, not zero; observed_main_plus_child_usage covers linked threads actually observed only.",
                "input includes cached input; output includes reasoning output; total_tokens is input_tokens + output_tokens. Subsets are never added again.",
                "Execution seconds are model-process wall time recorded by the harness, excluding independent checker time; they are not token generation latency.",
                "No currency/quota conversion is observed or estimated. This exploratory sample supports observed differences only, not statistical significance or a general ranking."]}


def compact(summary: dict) -> dict:
    return {key: summary[key] for key in ("status", "expected_samples", "finished_samples", "running_samples", "pending_samples", "unreadable_samples")} | {
        "groups": {arm: {"finished": group["receipt_states"].get("finished", 0),
                         "first_auto": group["first_turn_automatic_business_pass"],
                         "protocol_auto": group["after_protocol_automatic_business_pass"],
                         "budget_censored": group["budget_censored"]["pass"],
                         "execution_errors": group["execution_error"]["pass"],
                         "execution_seconds_observed_sum": group["execution_seconds"]["observed_sum"]}
                   for arm, group in summary["groups"].items()}}


def fmt(value: Any) -> str:
    if value is None:
        return "未知"
    if isinstance(value, bool):
        return "通过" if value else "未通过"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def verdict(counts: dict) -> str:
    return f'{counts["pass"]} 通过 / {counts["fail"]} 未通过 / {counts["unknown"]} 未知'


def flags(counts: dict) -> str:
    return f'{counts["pass"]} 是 / {counts["fail"]} 否 / {counts["unknown"]} 未知'


def md_stats(value: dict) -> str:
    return f'{fmt(value["observed_sum"])} / {fmt(value["observed_mean"])} / {fmt(value["observed_median"])} ({value["n_known"]}/{value["n"]})'


def render_markdown(summary: dict) -> str:
    lines = ["# 三组 Skill 对照观察汇总", "", f'生成时间：{summary["generated_utc"]}', "",
             f'状态：**{summary["status"]}**；注册 {summary["expected_samples"]}，已结束 {summary["finished_samples"]}，运行中 {summary["running_samples"]}，待开始 {summary["pending_samples"]}，收据暂不可读 {summary["unreadable_samples"]}。', "",
             "实验尚未全部结束，当前只展示观测进度，不作组间结论。" if summary["status"] == "running" else "本表只描述本次已观测差异，不作统计显著性判断或普遍排名。", "",
             "自动检查与盲审分列；盲审缺失保留未知。token 总量仅为输入加输出，缓存输入和推理输出均为其中子项。金额和配额成本未观测。", "",
             "## 组内业务检查", "", "| 组 | 已结束 / 注册 | 首轮自动检查 | 协议后自动检查 | 预算截断 | 执行错误 |", "|---|---:|---|---|---:|---:|"]
    for arm, group in summary["groups"].items():
        lines.append(f'| {arm} | {group["receipt_states"].get("finished", 0)} / {group["samples"]} | {verdict(group["first_turn_automatic_business_pass"])} | {verdict(group["after_protocol_automatic_business_pass"])} | {flags(group["budget_censored"])} | {flags(group["execution_error"])} |')
    lines += ["", "## 组内用量与耗时", "", "以下各格为已观测样本的 sum / mean / median（已知数 / 注册数）。完整样本统计另存于 JSON；有未知时其 sum/mean/median 为 null。", "",
              "主+子完整值要求子用量有观测；“观测到的主+子”仅覆盖已发现的原生线程，可能遗漏未观测子线程。未发现子线程不会被计为零。", "",
              "| 组 | 主原生 total token | 子原生 total token | 主+子完整原生 token | 观测到的主+子原生 token | 模型执行秒数 | 合成用户回复数 |", "|---|---|---|---|---|---|---|"]
    for arm, group in summary["groups"].items():
        totals = group["native_usage"]
        cells = [arm] + [md_stats(totals[key]["total_tokens"]) for key in ("main_usage", "child_usage", "main_plus_child_usage", "observed_main_plus_child_usage")]
        cells += [md_stats(group["execution_seconds"]), md_stats(group["synthetic_user_replies"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## 逐题逐次观测", "", "| 题目 | 次数 | 组 | 收据状态 | 首轮自动 | 协议后自动 | 首轮盲审 | 协议后盲审 | 执行秒数 | 合成回复 | 预算截断 | 执行错误 |", "|---|---:|---|---|---|---|---|---|---:|---:|---|---|"]
    for row in summary["samples"]:
        cells = [row["task"], row["repeat"], row["arm"], f'{row["receipt_state"]} / {row["receipt_status"] or "—"}', row["first_turn_automatic_business_pass"], row["after_protocol_automatic_business_pass"], row["blind_review"]["first_turn_business_pass"], row["blind_review"]["after_protocol_business_pass"], row["execution_seconds"], row["synthetic_user_replies"], "是" if row["budget_censored"] is True else "否" if row["budget_censored"] is False else None, "是" if row["execution_error"] is True else "否" if row["execution_error"] is False else None]
        lines.append("| " + " | ".join(fmt(c) for c in cells) + " |")
    lines += ["", "## 读取与解释边界", "", "- 读取 registration.json、runs/*/receipt.json 和可选 blind-review-results.json；不读取认证、配置、会话或模型事件原文。", "- 原生主线程与子线程逐次累计值由 metrics.py 提供；本工具不重新叠加轮次累计值。exec 回退用量只保留在 JSON 的 reported_usage 中。", "- 模型执行秒数来自 harness 的模型进程墙钟时间，不含独立检查耗时。", "- 预算截断和执行错误单独列出；自动检查通过不等同于外部生产发布成功。", "- 原始盲审字段保留于 JSON；本工具不从备注猜测业务结论。"]
    if summary["warnings"]:
        lines += ["", "读取警告："] + [f"- {warning}" for warning in summary["warnings"]]
    return "\n".join(lines) + "\n"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_summary(root: Path) -> dict:
    registration_path = root / "registration.json"
    registration = read_json(registration_path)
    if not isinstance(registration, dict):
        raise ValueError("registration.json is not an object")
    receipts, errors = {}, {}
    for path in sorted((root / "runs").glob("*/receipt.json")):
        try:
            value = read_json(path)
            if not isinstance(value, dict):
                raise ValueError("receipt is not an object")
            receipts[path.parent.name] = value
        except (OSError, ValueError) as exc:
            errors[path.parent.name] = f"receipt_read_error:{type(exc).__name__}"
    review_path = root / "blind-review-results.json"
    review_error = None
    try:
        reviews = read_json(review_path) if review_path.exists() else None
    except (OSError, ValueError) as exc:
        reviews, review_error = None, f"blind_review_read_error:{type(exc).__name__}"
    result = build_summary(registration, receipts, reviews, errors)
    result["registration_sha256"] = hashlib.sha256(registration_path.read_bytes()).hexdigest()
    if review_error:
        result["warnings"].append(review_error)
    return result


def atomic_write(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".summarize-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def self_test():
    registration = {"arms": ["a", "b"], "blocks": [{"task": "task", "repeat": n, "arms": ["a", "b"]} for n in (1, 2)]}
    def native(inp, out, cached=0, reasoning=0):
        return {"input_tokens": inp, "output_tokens": out, "cached_input_tokens": cached, "reasoning_output_tokens": reasoning}
    receipts = {"task-r1-a": {"id": "task-r1-a", "task": "task", "repeat": 1, "arm": "a", "status": "business_complete", "ended_utc": "done", "turns": [{"number": 1, "business_completed": True, "turn_completed": True, "errors": [], "returncode": 0}], "execution_seconds": 4, "synthetic_user_replies": 0, "metrics": {"sessions": [{"role": "main", "usage_source": "native_last_thread_cumulative", "usage": native(100, 20, 70, 10)}, {"role": "child", "usage_source": "native_last_thread_cumulative", "usage": native(30, 5, 10, 2)}], "child_usage": native(30, 5, 10, 2), "child_observability": {"coverage": "observed_threads_only"}}},
                "task-r2-a": {"id": "task-r2-a", "task": "task", "repeat": 2, "arm": "a", "status": "starting", "turns": [], "synthetic_user_replies": 0},
                "task-r1-b": {"id": "task-r1-b", "task": "task", "repeat": 1, "arm": "b", "status": "budget_censored", "ended_utc": "done", "turns": [{"number": 1, "business_completed": False, "budget_censored": True, "returncode": 1, "turn_completed": False}], "metrics": {"sessions": [{"role": "main", "usage_source": "exec_per_turn_fallback", "usage": native(80, 9)}]}}}
    result = build_summary(registration, receipts, {"reviews": [{"run_id": "task-r1-a", "first_turn_business_pass": False, "note": "Explicit verdict wins over automatic only in its own field."}]})
    assert result["status"] == "running" and result["finished_samples"] == 2 and result["pending_samples"] == 1
    a = result["groups"]["a"]
    total = a["native_usage"]["observed_main_plus_child_usage"]["total_tokens"]
    assert total["sum"] is None and total["observed_sum"] == 155 and total["n_known"] == 1
    assert a["execution_seconds"]["sum"] is None and a["execution_seconds"]["observed_mean"] == 4
    assert result["samples"][0]["first_turn_automatic_business_pass"] is True
    assert result["samples"][0]["blind_review"]["first_turn_business_pass"] is False
    assert result["groups"]["b"]["native_usage"]["main_usage"]["total_tokens"]["observed_sum"] is None
    assert result["groups"]["b"]["budget_censored"]["pass"] == 1
    assert result["groups"]["b"]["execution_error"]["pass"] == 0
    assert native_usage({"sessions": [{"role": "main", "usage_source": "native_last_thread_cumulative", "usage": native(10, 2)}]})["main_plus_child_usage"] is None
    assert stats([])["sum"] is None and stats([None])["observed_sum"] is None
    assert stats([0, 2])["sum"] == 2 and stats([0, 2])["median"] == 1
    assert "不作组间结论" in render_markdown(result)
    assert number(float("nan")) is None and number(-1) is None and number(True) is None
    single_registration = {"arms": ["a"], "blocks": [{"task": "task", "repeat": 1, "arms": ["a"]}]}
    finished = build_summary(single_registration, {"task-r1-a": receipts["task-r1-a"]}, {"reviews": {"task-r1-a": {"note": "Looks good"}}})
    assert finished["status"] == "complete"
    assert finished["groups"]["a"]["native_usage"]["main_plus_child_usage"]["total_tokens"]["sum"] == 155
    assert finished["samples"][0]["blind_review"]["first_turn_business_pass"] is None
    assert "不作组间结论" not in render_markdown(finished)
    running_receipt = {**receipts["task-r1-a"], "ended_utc": None}
    running = build_summary(single_registration, {"task-r1-a": running_receipt})
    assert running["samples"][0]["first_turn_automatic_business_pass"] is True
    assert running["samples"][0]["after_protocol_automatic_business_pass"] is None
    duplicate_warnings = []
    duplicate = parse_reviews({"reviews": [{"run_id": "x", "first_turn_business_pass": True}, {"run_id": "x", "first_turn_business_pass": False}]}, duplicate_warnings)
    assert duplicate["x"]["first_turn_business_pass"] is None and duplicate_warnings
    print(json.dumps({"self_test": "passed", "synthetic_samples": 4}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output-dir", type=Path, help="Default: --root; only summary.json and summary.md are written")
    parser.add_argument("--brief", action="store_true", help="Print compact JSON only; do not write report files")
    parser.add_argument("--self-test", action="store_true", help="Run pure synthetic in-memory assertions without reading trial files")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    summary = load_summary(args.root)
    if args.brief:
        print(json.dumps(compact(summary), ensure_ascii=False, separators=(",", ":")))
        return
    output = args.output_dir or args.root
    atomic_write(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    atomic_write(output / "summary.md", render_markdown(summary))
    print(json.dumps({"status": summary["status"], "finished_samples": summary["finished_samples"], "expected_samples": summary["expected_samples"], "outputs": [str(output / "summary.json"), str(output / "summary.md")]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
