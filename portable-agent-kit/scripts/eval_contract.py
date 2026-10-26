#!/usr/bin/env python3
"""Validate and summarize a frozen, model-agnostic evaluation contract."""
from __future__ import annotations
import argparse, hashlib, json, statistics, sys
from pathlib import Path

VERSION = "0.13"
ROUTES = {"QUICK", "BOUNDED", "GOVERNED", "MAINTENANCE"}
BOUNDARIES = {"AUTO", "ASK", "STOP", "DEFER"}
EVIDENCE = {"DONE", "NEEDS_EVIDENCE", "BLOCKED", "BUSINESS_DECISION"}
STATUSES = {"completed", "failed", "blocked", "unknown", "cancelled"}
METRICS = {"first_pass_success", "final_success", "user_correction_count",
           "recoverable_blocker_handoff_rate", "handoff_precision", "recovery_completion",
           "independent_progress", "stale_side_effect_rate",
           "unknown_reported_as_success_rate", "execution_seconds",
           "input_tokens", "cached_input_tokens", "output_tokens"}
RUNTIME_MAP = {
    "route": {"QUICK": "QUICK_PATCH", "BOUNDED": "BOUNDED_CHANGE", "GOVERNED": "GOVERNED_DELIVERY", "MAINTENANCE": None},
    "boundary": {"AUTO": "READY", "ASK": "NEEDS_INPUT", "STOP": "BLOCKED_OR_UNKNOWN", "DEFER": "NEEDS_CONTEXT"},
    "evidence": {"DONE": "PASS_REQUIRED", "NEEDS_EVIDENCE": "INCOMPLETE", "BLOCKED": "BLOCKED", "BUSINESS_DECISION": "NEEDS_INPUT"},
}

class ContractError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise ContractError(message)

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def validate_contract(c):
    require(isinstance(c, dict), "contract must be an object")
    require(c.get("schema_version") == VERSION, "unsupported schema_version")
    require(isinstance(c.get("contract_id"), str) and c["contract_id"], "contract_id required")
    require(isinstance(c.get("objective"), str) and c["objective"], "objective required")
    arms, tasks = c.get("arms"), c.get("tasks")
    require(isinstance(arms, list) and arms, "arms must be a non-empty list")
    require(isinstance(tasks, list) and tasks, "tasks must be a non-empty list")
    arm_ids = [x.get("id") if isinstance(x, dict) else None for x in arms]
    task_ids = [x.get("id") if isinstance(x, dict) else None for x in tasks]
    require(all(isinstance(x, str) and x for x in arm_ids), "every arm needs an id")
    require(all(isinstance(x, str) and x for x in task_ids), "every task needs an id")
    require(len(set(arm_ids)) == len(arm_ids), "arm ids must be unique")
    require(len(set(task_ids)) == len(task_ids), "task ids must be unique")
    repeats = c.get("repetitions", 1)
    require(type(repeats) is int and repeats > 0, "repetitions must be positive")
    metrics = c.get("metrics")
    require(isinstance(metrics, list) and metrics and all(x in METRICS for x in metrics), "invalid metrics")
    freeze = c.get("freeze", {})
    require(isinstance(freeze, dict) and isinstance(freeze.get("seed"), (str, int)), "freeze.seed required")
    require(isinstance(freeze.get("evaluator_sha256"), str) and freeze["evaluator_sha256"], "evaluator hash required")
    for task in tasks:
        require(isinstance(task, dict), "task must be an object")
        require(isinstance(task.get("objective"), str) and task["objective"], f"task {task.get('id')} objective required")
        obs = task.get("expected_observations")
        require(isinstance(obs, list) and obs, f"task {task.get('id')} needs observations")
        ids = [x.get("id") if isinstance(x, dict) else None for x in obs]
        require(all(isinstance(x, str) and x for x in ids) and len(set(ids)) == len(ids), f"task {task.get('id')} observation ids invalid")
        require(task.get("risk") in {"low", "medium", "high"}, f"task {task.get('id')} risk invalid")
        require(isinstance(task.get("required_capabilities", []), list), f"task {task.get('id')} capabilities invalid")
    return {"arms": arm_ids, "tasks": task_ids, "repetitions": repeats}

def opt_bool(v, field):
    require(v is None or type(v) is bool, f"{field} must be boolean or null")

def opt_num(v, field):
    require(v is None or (not isinstance(v, bool) and isinstance(v, (int, float))), f"{field} must be numeric or null")

def validate_receipts(contract, path):
    meta = validate_contract(contract)
    contract_path = Path(path).parent / "contract.json"
    contract_sha = digest(contract_path) if contract_path.exists() else None
    rows, seen = [], set()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read receipts {path}: {exc}") from exc
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"receipt line {n} is not JSON: {exc}") from exc
        require(isinstance(row, dict), f"receipt line {n} must be object")
        require(row.get("schema_version") == VERSION, f"receipt line {n} schema mismatch")
        for key in ("trial_id", "contract_id", "task_id", "arm_id"):
            require(isinstance(row.get(key), str) and row[key], f"receipt line {n} missing {key}")
        require(row["contract_id"] == contract["contract_id"], f"receipt line {n} contract mismatch")
        require(row["task_id"] in meta["tasks"] and row["arm_id"] in meta["arms"], f"receipt line {n} unknown task/arm")
        repeat = row.get("repeat")
        require(type(repeat) is int and 1 <= repeat <= meta["repetitions"], f"receipt line {n} repeat invalid")
        require(row["trial_id"] not in seen, f"duplicate trial_id {row['trial_id']}")
        seen.add(row["trial_id"])
        require(row.get("status") in STATUSES, f"receipt line {n} status invalid")
        require(row.get("evidence_status") in EVIDENCE, f"receipt line {n} evidence_status invalid")
        if row.get("route") is not None:
            require(row["route"] in ROUTES, f"receipt line {n} route invalid")
        if row.get("boundary") is not None:
            require(row["boundary"] in BOUNDARIES, f"receipt line {n} boundary invalid")
        for field in ("first_pass_success", "final_success", "recoverable_blocker", "handoff_required", "handoff_justified", "recovery_completed", "independent_progress", "stale_side_effect", "unknown_reported_as_success"):
            opt_bool(row.get(field), f"receipt line {n} {field}")
        for field in ("user_correction_count", "execution_seconds", "input_tokens", "cached_input_tokens", "output_tokens"):
            opt_num(row.get(field), f"receipt line {n} {field}")
        refs = row.get("evidence_refs", [])
        require(isinstance(refs, list), f"receipt line {n} evidence_refs invalid")
        if row.get("final_success") is True:
            require(refs, f"receipt line {n} success needs evidence_refs")
        if contract_sha and row.get("contract_sha256") is not None:
            require(row["contract_sha256"] == contract_sha, f"receipt line {n} contract hash drift")
        rows.append(row)
    return rows

def rate(values, predicate=bool):
    known = [x for x in values if x is not None]
    return {"value": (sum(bool(predicate(x)) for x in known) / len(known)) if known else None,
            "known": len(known), "unknown": len(values) - len(known)}

def summarize(contract, rows):
    meta = validate_contract(contract)
    expected = {(t, a, r) for t in meta["tasks"] for a in meta["arms"] for r in range(1, meta["repetitions"] + 1)}
    actual = {(r["task_id"], r["arm_id"], r["repeat"]) for r in rows}
    missing = sorted(expected - actual)
    groups = {}
    for arm in meta["arms"]:
        group = [r for r in rows if r["arm_id"] == arm]
        values = lambda name: [r.get(name) for r in group]
        corrections = [x for x in values("user_correction_count") if x is not None]
        seconds = [x for x in values("execution_seconds") if x is not None]
        recoverable = [r for r in group if r.get("recoverable_blocker") is not None]
        handoffs = [r for r in group if r.get("handoff_required") is True and r.get("handoff_justified") is not None]
        recovered = [r for r in group if r.get("recoverable_blocker") is True and r.get("recovery_completed") is not None]
        groups[arm] = {
            "samples": len(group),
            "first_pass_success": rate(values("first_pass_success")),
            "final_success": rate(values("final_success")),
            "mean_user_corrections": statistics.mean(corrections) if corrections else None,
            "recoverable_blocker_handoff_rate": rate([r.get("handoff_required") for r in recoverable]),
            "handoff_precision": rate([r.get("handoff_justified") for r in handoffs]),
            "recovery_completion": rate([r.get("recovery_completed") for r in recovered]),
            "stale_side_effect_rate": rate(values("stale_side_effect")),
            "unknown_reported_as_success_rate": rate(values("unknown_reported_as_success")),
            "mean_execution_seconds": statistics.mean(seconds) if seconds else None,
        }
    return {"schema_version": VERSION, "contract_id": contract["contract_id"], "expected_trials": len(expected),
            "observed_trials": len(actual), "missing_trials": [{"task_id": t, "arm_id": a, "repeat": r} for t, a, r in missing],
            "status": "COMPLETE" if not missing else "INCOMPLETE", "arms": groups}

def decision_map(route=None, boundary=None, evidence=None):
    result = {"runtime_map": RUNTIME_MAP}
    if route is not None:
        require(route in ROUTES, "route invalid")
        result["route"] = RUNTIME_MAP["route"][route]
    if boundary is not None:
        require(boundary in BOUNDARIES, "boundary invalid")
        result["boundary"] = RUNTIME_MAP["boundary"][boundary]
    if evidence is not None:
        require(evidence in EVIDENCE, "evidence invalid")
        result["evidence"] = RUNTIME_MAP["evidence"][evidence]
    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate"); v.add_argument("contract", type=Path); v.add_argument("--receipts", type=Path)
    s = sub.add_parser("summarize"); s.add_argument("contract", type=Path); s.add_argument("receipts", type=Path); s.add_argument("--allow-incomplete", action="store_true")
    m = sub.add_parser("map"); m.add_argument("--route", choices=sorted(ROUTES)); m.add_argument("--boundary", choices=sorted(BOUNDARIES)); m.add_argument("--evidence", choices=sorted(EVIDENCE))
    args = parser.parse_args(argv)
    try:
        if args.command == "map":
            print(json.dumps(decision_map(args.route, args.boundary, args.evidence), ensure_ascii=False, indent=2)); return 0
        contract = load_json(args.contract)
        meta = validate_contract(contract)
        if args.command == "validate":
            result = {"status": "VALID", "schema_version": VERSION, "contract_id": contract["contract_id"], "tasks": len(meta["tasks"]), "arms": len(meta["arms"]), "repetitions": meta["repetitions"]}
            if args.receipts:
                result["receipts"] = len(validate_receipts(contract, args.receipts))
        else:
            result = summarize(contract, validate_receipts(contract, args.receipts))
            if result["status"] == "INCOMPLETE" and not args.allow_incomplete:
                print(json.dumps(result, ensure_ascii=False, indent=2)); return 2
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except ContractError as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
