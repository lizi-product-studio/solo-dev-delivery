"""Independent read-only audit of observed native injection and runtime metadata.

Reads run receipts/config logs/events plus each receipt home's sessions/*.jsonl.
Never reads auth.json, model task files, skill bodies on disk, or writes receipts.
Only the caller-selected audit report may be written. Live runs are snapshots.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib

from metrics import aggregate_run

TARGET_MODEL = "gpt-6.1-sol"
TARGET_EFFORT = "ultra"
GLOBAL_MARKERS = ("全局任务协作偏好",)
MEMORY_MARKERS = ("MEMORY_SUMMARY BEGINS", "MEMORY_SUMMARY ENDS",
                  "You have access to a memory folder", "Memory layout (general -> specific)")
CATALOG_HEADING = re.compile(r"(?m)^#{1,6}\s+Available skills\s*$")
NEXT_HEADING = re.compile(r"(?m)^#{1,6}\s+|</skills_instructions>")
CATALOG_ENTRY = re.compile(r"^\s*-\s+([^:]+):\s*(.*)$")
CATALOG_FILE = re.compile(r"\(file:\s*([^\r\n]+?)\)\s*$")


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict))
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return ""


def extract_catalogs(text):
    """Restrict matching to bullet names and file paths inside Available skills.

    Descriptions and noncatalog prose may legitimately mention competitors.
    This function is called only for model-visible DEVELOPER messages.
    """
    result = []
    for match in CATALOG_HEADING.finditer(text):
        end = NEXT_HEADING.search(text, match.end())
        body = text[match.end():end.start() if end else len(text)]
        entries = []
        unparsed = []
        for line in body.splitlines():
            if not line.strip():
                continue
            item = CATALOG_ENTRY.match(line)
            if not item:
                if line.lstrip().startswith("-"):
                    unparsed.append("unrecognized_bullet")
                continue
            name, description = item.groups()
            file_match = CATALOG_FILE.search(description)
            path = file_match.group(1).strip() if file_match else None
            normalized = (path or "").replace("\\", "/").lower()
            parts = normalized.split("/")
            lower_name = name.strip().lower()
            solo = lower_name == "solo-dev-delivery" or "solo-dev-delivery" in parts
            superpowers = lower_name == "using-superpowers" or "superpowers" in parts
            entries.append({"name": name.strip(), "path": path,
                "treatment": "solo" if solo else "superpowers" if superpowers else None})
        result.append({"sha256": hashlib.sha256(body.encode()).hexdigest(), "entries": entries,
                       "parse_status": "unknown" if unparsed or not entries else "parsed"})
    return result


def catalog_verdict(arm, catalogs):
    observed = [entry for c in catalogs for entry in c["entries"]]
    treatments = {e["treatment"] for e in observed if e["treatment"]}
    forbidden = {"solo", "superpowers"} - ({arm} if arm in ("solo", "superpowers") else set())
    wrong = sorted(treatments & forbidden)
    if wrong:
        return {"status": "fail", "reason": "forbidden_treatment_in_developer_catalog", "forbidden": wrong}
    if not catalogs or any(c["parse_status"] != "parsed" for c in catalogs):
        return {"status": "unknown", "reason": "missing_or_unparsed_developer_catalog", "forbidden": []}
    if arm in ("solo", "superpowers"):
        if any(not any(e["treatment"] == arm for e in c["entries"]) for c in catalogs):
            return {"status": "fail", "reason": "expected_treatment_absent_from_observed_catalog", "forbidden": []}
    return {"status": "pass", "reason": "observed_catalog_matches_arm", "forbidden": []}


def _rollup(states):
    return "fail" if "fail" in states else "unknown" if not states or "unknown" in states else "pass"


def audit_native_file(path, arm):
    meta = {}
    catalogs = {}
    guidance_hits = []
    invalid = []
    guidance_count = 0
    latest = None
    rows = 0

    def guidance(text, source, line_no):
        nonlocal guidance_count
        if not text:
            return
        guidance_count += 1
        for kind, markers in (("global_agents", GLOBAL_MARKERS), ("memory_summary", MEMORY_MARKERS)):
            for marker in markers:
                if marker in text:
                    guidance_hits.append({"kind": kind, "marker": marker, "source": source, "line": line_no})

    try:
        handle = path.open(encoding="utf-8-sig")
    except OSError as exc:
        return {"file": str(path), "catalog_check": {"status": "unknown", "reason": type(exc).__name__},
                "guidance_check": "unknown", "invalid_lines": [], "catalogs": []}
    with handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                invalid.append(line_no)
                continue
            if not isinstance(row, dict):
                continue
            rows += 1
            timestamp = row.get("timestamp")
            if isinstance(timestamp, str):
                latest = max(latest or timestamp, timestamp)
            kind, payload = row.get("type"), row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if kind == "session_meta":
                meta = {"thread_id": payload.get("id") or payload.get("thread_id"),
                        "session_id": payload.get("session_id"), "source": payload.get("source")}
                guidance(text_content(payload.get("base_instructions")), "session_meta.base_instructions", line_no)
            elif kind == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                text = text_content(payload.get("content"))
                if role in ("developer", "system"):
                    guidance(text, role, line_no)
                elif role == "user" and text.lstrip().startswith(("# AGENTS.md instructions", "<environment_context>", "<INSTRUCTIONS>")):
                    # Skill-expanded user messages (<skill>...) are deliberately excluded.
                    guidance(text, "user_injected_guidance", line_no)
                if role == "developer":
                    for catalog in extract_catalogs(text):
                        key = catalog["sha256"]
                        if key not in catalogs:
                            catalogs[key] = {**catalog, "first_line": line_no, "occurrences": 0}
                        catalogs[key]["occurrences"] += 1
    values = list(catalogs.values())
    return {"file": str(path), **meta, "row_count": rows, "latest_native_record_at": latest,
        "catalogs": values, "catalog_check": catalog_verdict(arm, values),
        "guidance_messages_checked": guidance_count, "guidance_hits": guidance_hits,
        "guidance_check": "fail" if guidance_hits else "pass" if guidance_count and meta else "unknown",
        "invalid_lines": invalid,
        "limits": "Catalog/guidance injection audit only; tool-return Skill prose and candidate text are excluded. Missing records remain unknown."}


def audit_run(run_dir):
    receipt_path = run_dir / "receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"id": run_dir.name, "status": "unknown", "error": type(exc).__name__}
    arm = receipt.get("arm")
    home_value = receipt.get("home")
    home = Path(home_value) if home_value else None
    native_paths = sorted((home / "sessions").rglob("*.jsonl")) if home and (home / "sessions").exists() else []
    native = [audit_native_file(path, arm) for path in native_paths]
    metrics = aggregate_run(sorted(run_dir.glob("turn-*.events.jsonl")), home) if home else None
    mismatches = []
    missing_context = []
    for session in (metrics or {}).get("sessions", []):
        models, efforts = session.get("observed_models"), session.get("observed_efforts")
        if not models or not efforts:
            missing_context.append(session["thread_id"])
        if models and models != [TARGET_MODEL]:
            mismatches.append({"thread_id": session["thread_id"], "field": "model", "observed": models})
        if efforts and efforts != [TARGET_EFFORT]:
            mismatches.append({"thread_id": session["thread_id"], "field": "effort", "observed": efforts})
    model_state = "fail" if mismatches else "unknown" if not metrics or not metrics["sessions"] or missing_context else "pass"
    config_observed = None
    try:
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        parsed = tomllib.loads(config["text"])
        config_observed = {"model": parsed.get("model"), "effort": parsed.get("model_reasoning_effort"),
                           "memories": parsed.get("features", {}).get("memories"),
                           "use_memories": parsed.get("memories", {}).get("use_memories")}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    abnormal = [{"turn": t.get("number"), "returncode": t.get("returncode"),
                  "turn_completed": t.get("turn_completed"), "budget_censored": t.get("budget_censored", False),
                  "error_types": [e.get("type") for e in t.get("errors", []) if isinstance(e, dict)]}
                 for t in receipt.get("turns", [])
                 if t.get("returncode") != 0 or not t.get("turn_completed") or t.get("budget_censored")]
    terminal = [{"thread_id": s["thread_id"], "role": s["role"],
                 "native_terminal_observed": s.get("native_terminal_observed"),
                 "last_cumulative_usage_at": s.get("last_cumulative_usage_at"),
                 "usage_source": s.get("usage_source"), "has_usage": s.get("usage") is not None}
                for s in (metrics or {}).get("sessions", [])]
    return {"id": receipt.get("id", run_dir.name), "arm": arm, "receipt_status": receipt.get("status"),
        "run_ended_utc": receipt.get("ended_utc"), "is_live_snapshot": not bool(receipt.get("ended_utc")),
        "catalog_check": _rollup([n["catalog_check"]["status"] for n in native]),
        "global_guidance_check": _rollup([n["guidance_check"] for n in native]),
        "actual_model_effort_check": model_state, "model_effort_mismatches": mismatches,
        "missing_model_effort_threads": missing_context, "config_observed": config_observed,
        "observed_models": (metrics or {}).get("observed_models"),
        "observed_efforts": (metrics or {}).get("observed_efforts"),
        "native_threads": native, "thread_usage_observation": terminal,
        "child_observability": (metrics or {}).get("child_observability"),
        "metrics_captured_utc": (metrics or {}).get("captured_utc"),
        "receipt_metrics_captured_utc": receipt.get("metrics", {}).get("captured_utc"),
        "metrics_warnings": (metrics or {}).get("warnings"), "abnormal_exits": abnormal,
        "non_normal_receipt_status": receipt.get("status") if receipt.get("status") not in
            ("business_complete", "not_complete_after_protocol", "starting") else None}


def audit_runs(root):
    root = Path(root)
    runs = [audit_run(p) for p in sorted((root / "runs").iterdir()) if p.is_dir()] if (root / "runs").exists() else []
    summary = {field: dict(Counter(r.get(field, "unknown") for r in runs)) for field in
               ("catalog_check", "global_guidance_check", "actual_model_effort_check")}
    return {"schema_version": 1, "captured_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Observed native developer Available skills catalogs, injected guidance, native turn contexts, lifecycle and run receipts.",
        "target_model": TARGET_MODEL, "target_effort": TARGET_EFFORT, "observed_runs": len(runs),
        "finished_runs": sum(not r.get("is_live_snapshot", True) for r in runs), "summary": summary, "runs": runs,
        "attribution": {"supported": "通过的已观测会话可证明三组实际注入目录符合分组、未观测到指定全局AGENTS/记忆摘要标记，且原生上下文记录的模型与强度一致。结合冻结任务和统一协议，可比较本轮这些任务中的交付、交互与已观测资源差异。",
            "not_supported": "不能证明完整文件系统隔离、未发生任何越界读取、隐藏子进程均已计量、模型后端权重固定，或把小样本结果外推为普遍优胜。也不能把三组共同的宿主指导、权限、人工批准协议与工具实现的贡献单独归因给Skill。目录未加载不等于模型从未见过相关方法。",
            "unknown_policy": "缺失目录、上下文、子线程日志或终态均保留unknown/None；正在运行的记录只是截面，非最终失败或完整成本。"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_runs(args.root)
    if args.output:
        # Restrict all writes to the separate audit directory, away from frozen
        # sources, run receipts, native logs, fixtures and candidate workspaces.
        target = args.output.resolve()
        audit_dir = (args.root / "audits").resolve()
        if audit_dir not in target.parents or target.suffix != ".json":
            raise SystemExit("Audit output must be a separate .json report under ROOT/audits/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"observed_runs": report["observed_runs"], "finished_runs": report["finished_runs"], "summary": report["summary"]}, ensure_ascii=False))
