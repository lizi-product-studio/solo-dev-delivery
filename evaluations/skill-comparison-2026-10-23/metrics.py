"""Read-only Codex metrics; home must be dedicated to exactly one benchmark run.

Only events_paths and home/sessions/**/*.jsonl are read. Credentials/configs and
message text are never returned. Native per-thread final cumulative usage wins
over exec turn usage. A session_meta.id is the thread key: session_id may be
shared by all descendants and MUST NOT be used to deduplicate threads.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    result = {key: (value[key] if isinstance(value.get(key), int)
                    and not isinstance(value[key], bool) and value[key] >= 0 else None)
              for key in FIELDS}
    if all(value is None for value in result.values()):
        return None
    inp, cached = result["input_tokens"], result["cached_input_tokens"]
    result["uncached_input_tokens"] = inp - cached if inp is not None and cached is not None and cached <= inp else None
    # Cached input and reasoning are subsets, not additional billable totals.
    result["total_tokens"] = inp + result["output_tokens"] if inp is not None and result["output_tokens"] is not None else None
    return result


def _sum_usage(values: list[dict | None]) -> dict | None:
    if not values or all(v is None for v in values):
        return None
    keys = (*FIELDS, "uncached_input_tokens", "total_tokens")
    return {key: sum(v[key] for v in values) if all(v is not None and v.get(key) is not None for v in values) else None
            for key in keys}


def _time(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return float("-inf")


def _rows(path: Path, warnings: list[str]):
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    warnings.append(f"invalid_json:{path.name}:{line_no}")
                    continue
                if isinstance(row, dict):
                    yield line_no, row
    except OSError as exc:
        warnings.append(f"unreadable:{path.name}:{type(exc).__name__}")


def _parent(value: Any) -> str | None:
    if isinstance(value, dict):
        if isinstance(value.get("parent_thread_id"), str):
            return value["parent_thread_id"]
        for item in value.values():
            found = _parent(item)
            if found:
                return found
    return None


def _spawn_info(row: dict) -> tuple[bool, set[str]]:
    """Inspect tool metadata only, never regex-search prompts or command strings."""
    found: set[str] = set()
    spawned = False

    def walk(value):
        nonlocal spawned
        if not isinstance(value, dict):
            return
        kind = str(value.get("type", ""))
        name = str(value.get("name", value.get("tool", value.get("tool_name", ""))))
        is_spawn = name.replace("__", ".").split(".")[-1] == "spawn_agent"
        if is_spawn:
            spawned = True
        # receiver IDs appear on structured collaboration-tool events.
        if "collab" in kind or is_spawn:
            for key in ("receiver_thread_ids", "child_thread_ids"):
                ids = value.get(key, [])
                if isinstance(ids, list):
                    found.update(x for x in ids if isinstance(x, str))
            for key in ("child_thread_id", "spawned_thread_id", "agent_id"):
                if isinstance(value.get(key), str):
                    found.add(value[key])
        for key in ("item", "payload", "result"):
            walk(value.get(key))
    walk(row)
    return spawned, found


def aggregate_run(events_paths, home) -> dict:
    """Aggregate one run's main and child usage without summing cumulatives.

    events_paths may include multiple turns/resume logs. Every native thread is
    counted once using its LAST timestamped cumulative total across all turns.
    Exec usage is a per-turn fallback only when native usage is unavailable.
    Missing child usage remains None. Observed model/effort values are native
    runtime evidence, not a claim about an immutable backend model revision.
    """
    if isinstance(events_paths, (str, Path)):
        events_paths = [events_paths]
    paths = [Path(p) for p in events_paths]
    home = Path(home)
    warnings: list[str] = []
    sessions: dict[str, dict] = {}
    root_ids: set[str] = set()
    expected_children: set[str] = set()
    spawn_seen = False
    ended_roots: set[str] = set()
    sequence = 0

    def session(sid):
        return sessions.setdefault(sid, {"thread_id": sid, "parent_thread_id": None,
            "paths": set(), "cumulative": None, "responses": {}, "turns": {},
            "contexts": {}, "native": False, "completed": False})

    def cumulative(item, value, timestamp, priority):
        nonlocal sequence
        parsed = _usage(value)
        if parsed is None:
            return
        sequence += 1
        order = (_time(timestamp), priority, sequence)
        if item["cumulative"] is None or order > item["cumulative"][0]:
            item["cumulative"] = (order, parsed, timestamp)

    # Native records contain per-thread totals, including partial/failed turns.
    native_paths = sorted((home / "sessions").rglob("*.jsonl")) if (home / "sessions").exists() else []
    for path in native_paths:
        rows = list(_rows(path, warnings))
        meta = next((r.get("payload", {}) for _, r in rows if r.get("type") == "session_meta"), {})
        sid = meta.get("id") or meta.get("thread_id")
        if not isinstance(sid, str):
            warnings.append(f"missing_thread_id:{path.name}")
            continue
        item = session(sid)
        item["native"] = True
        item["paths"].add(str(path))
        item["parent_thread_id"] = _parent(meta.get("source")) or _parent(meta.get("thread_source")) or item["parent_thread_id"]
        if item["parent_thread_id"]:
            expected_children.add(sid)
        for line_no, row in rows:
            kind, payload = row.get("type"), row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            spawned, ids = _spawn_info(row)
            spawn_seen |= spawned
            expected_children.update(ids)
            if kind == "turn_context":
                settings = ((payload.get("collaboration_mode") or {}).get("settings") or {}) if isinstance(payload.get("collaboration_mode"), dict) else {}
                model = payload.get("model") or settings.get("model")
                effort = payload.get("effort") or payload.get("reasoning_effort") or settings.get("reasoning_effort")
                context = {"turn_id": payload.get("turn_id"), "model": model if isinstance(model, str) else None,
                           "effort": effort if isinstance(effort, str) else None}
                item["contexts"][json.dumps(context, sort_keys=True)] = context
            elif kind == "token_usage_record":
                target = session(payload.get("thread_id") or sid)
                target["native"] = True
                cumulative(target, payload.get("thread_token_usage"), row.get("timestamp"), 2)
                response_id = payload.get("response_id")
                if isinstance(response_id, str) and _usage(payload.get("usage")) is not None:
                    target["responses"][response_id] = _usage(payload["usage"])
            elif kind == "event_msg":
                if payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    if isinstance(info, dict):
                        cumulative(item, info.get("total_token_usage", info.get("total_usage")), row.get("timestamp"), 1)
                if payload.get("type") in ("task_complete", "turn_complete", "task_aborted"):
                    item["completed"] = True

    # Deduplicate duplicate copies of an exec stream by whole-file content.
    seen_streams: set[str] = set()
    exec_turn_count = 0
    for path in paths:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = str(path)
        if digest in seen_streams:
            continue
        seen_streams.add(digest)
        current = None
        turn_index = 0
        for line_no, row in _rows(path, warnings):
            if row.get("type") == "thread.started" and isinstance(row.get("thread_id"), str):
                current = row["thread_id"]
                root_ids.add(current)
                session(current)
            spawned, ids = _spawn_info(row)
            spawn_seen |= spawned
            expected_children.update(ids)
            if row.get("type") in ("turn.completed", "turn.failed") and current:
                ended_roots.add(current)
                value = _usage(row.get("usage"))
                if value:
                    turn_index += 1
                    # Explicit turn IDs deduplicate overlaps across resumed captures.
                    turn_key = row.get("turn_id") or f"{digest}:{turn_index}"
                    session(current)["turns"][turn_key] = value
                    exec_turn_count += 1

    if not root_ids:
        roots = [sid for sid, item in sessions.items() if not item["parent_thread_id"]]
        if len(roots) == 1:
            root_ids.update(roots)
        elif roots:
            warnings.append("ambiguous_roots_without_exec_thread_started")

    output = []
    for sid, item in sorted(sessions.items()):
        if item["cumulative"] is not None:
            usage, source = item["cumulative"][1], "native_last_thread_cumulative"
        elif item["responses"]:
            usage, source = _sum_usage(list(item["responses"].values())), "native_deduplicated_response_usage"
        elif item["turns"]:
            usage, source = _sum_usage(list(item["turns"].values())), "exec_per_turn_fallback"
            warnings.append(f"exec_fallback_may_omit_partial_turn_or_reasoning:{sid}")
        else:
            usage, source = None, "missing"
        contexts = list(item["contexts"].values())
        role = "main" if sid in root_ids else "child" if item["parent_thread_id"] or sid in expected_children else "unlinked"
        output.append({"thread_id": sid, "parent_thread_id": item["parent_thread_id"], "role": role,
            "usage": usage, "usage_source": source, "native_files": sorted(item["paths"]),
            "native_terminal_observed": item["completed"] if item["native"] else None,
            "exec_turn_terminal_observed": sid in ended_roots,
            "last_cumulative_usage_at": item["cumulative"][2] if item["cumulative"] else None,
            "observed_models": sorted({c["model"] for c in contexts if c["model"]}) or None,
            "observed_efforts": sorted({c["effort"] for c in contexts if c["effort"]}) or None,
            "turn_contexts": contexts, "response_records": len(item["responses"]),
            "exec_usage_turns": len(item["turns"])})

    main = [x for x in output if x["role"] == "main"]
    children = [x for x in output if x["role"] == "child"]
    known_ids = set(sessions)
    missing_children = sorted(expected_children - known_ids)
    all_known = bool(main) and all(x["usage"] is not None for x in output)
    unlinked = [x["thread_id"] for x in output if x["role"] == "unlinked"]
    spawn_unresolved = spawn_seen and not expected_children
    # No child records is absence of observation, not zero child-token usage.
    child_usage = _sum_usage([x["usage"] for x in children]) if children and not missing_children and not spawn_unresolved else None
    coverage = "missing_child_records" if missing_children or spawn_unresolved else "unlinked_sessions" if unlinked else "observed_threads_only"
    if not native_paths:
        warnings.append("native_sessions_unavailable_child_usage_not_proven_complete")
    return {"schema_version": 1, "captured_utc": datetime.now(timezone.utc).isoformat(),
        "root_thread_ids": sorted(root_ids), "sessions": output,
        "main_usage": _sum_usage([x["usage"] for x in main]), "child_usage": child_usage,
        "observed_child_usage": _sum_usage([x["usage"] for x in children]),
        "observed_total_usage": _sum_usage([x["usage"] for x in output]),
        "observed_models": sorted({m for x in output for m in (x["observed_models"] or [])}) or None,
        "observed_efforts": sorted({e for x in output for e in (x["observed_efforts"] or [])}) or None,
        "child_observability": {"spawn_observed": spawn_seen, "observed_threads": len(children),
            "expected_thread_ids": sorted(expected_children), "missing_thread_ids": missing_children,
            "coverage": coverage, "known_threads_have_usage": all_known,
            "observed_children_terminal": all(sessions[x["thread_id"]]["completed"] for x in children) if children else None,
            "note": "No observed child is not a measured zero. Fresh dedicated home required; unrelated native threads would also be counted."},
        "native_session_files": len(native_paths), "exec_completed_usage_events": exec_turn_count,
        "ended_root_ids": sorted(ended_roots), "warnings": sorted(set(warnings)),
        "limits": ["Cumulative totals are selected once per thread, not added across turns or to exec usage.",
            "cached_input is included in input; reasoning_output is included in output.",
            "Observed totals cover discovered records only; missing child observations stay None.",
            "Runtime model IDs/efforts do not identify immutable backend weights; no currency/quota conversion."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--events", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = aggregate_run(args.events, args.home)
    rendered = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
