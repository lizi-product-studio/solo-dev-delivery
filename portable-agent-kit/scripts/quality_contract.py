#!/usr/bin/env python3
"""Validate bounded quality contracts without calling a model or external service.

The contracts turn five weak areas into explicit evidence requirements:
UI presentation, backend/system design, host integration, bounded project cycles,
and A/B cost/quality comparisons.  They are advisory evidence gates, not
runtime enforcement or production capacity proofs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True

FORMAT = {
    "ui": "ui-quality/1",
    "ui_strict": "ui-quality/2",
    "system": "system-design/1",
    "system_strict": "system-design/2",
    "host": "host-adapter/1",
    "autonomy": "autonomy-cycle/1",
    "autonomy_strict": "autonomy-cycle/2",
}


def _fail(message):
    raise ValueError(message)


def _obj(value, name):
    if not isinstance(value, dict):
        _fail(name + " must be an object")
    return value


def _list(value, name, minimum=1):
    if not isinstance(value, list) or len(value) < minimum:
        _fail(name + " must be a list with at least " + str(minimum) + " item(s)")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        _fail(name + " must be a non-empty string")
    return value.strip()


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        _fail(name + " must be a number >= " + str(minimum))
    return value


def _status(value, name):
    if value is True:
        return "PASS"
    if value is False or value is None:
        return "INCOMPLETE"
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in {"PASS", "INCOMPLETE", "FAIL", "UNKNOWN", "UNVERIFIED"}:
            return "INCOMPLETE" if upper in {"UNKNOWN", "UNVERIFIED"} else upper
    _fail(name + " must be true/false or PASS/INCOMPLETE/FAIL")


def _overall(statuses, missing=False):
    if "FAIL" in statuses:
        return "FAIL"
    if missing or any(state != "PASS" for state in statuses):
        return "INCOMPLETE"
    return "PASS"


def validate_ui(record):
    record = _obj(record, "record")
    ui_format = record.get("format")
    if ui_format not in {FORMAT["ui"], FORMAT["ui_strict"]}:
        _fail("record.format must be " + FORMAT["ui"] + " or " + FORMAT["ui_strict"])
    _text(record.get("task"), "task")
    _text(record.get("target_users"), "target_users")
    viewports = _list(record.get("viewports"), "viewports", 2)
    seen = set(); statuses = []; missing = False
    for row in viewports:
        row = _obj(row, "viewport")
        name = _text(row.get("name"), "viewport.name")
        if name in seen:
            _fail("duplicate viewport: " + name)
        seen.add(name)
        _number(row.get("width"), "viewport.width", 1)
        _number(row.get("height"), "viewport.height", 1)
        _text(row.get("evidence"), "viewport.evidence")
    paths = _list(record.get("paths"), "paths")
    for row in paths:
        row = _obj(row, "path")
        _text(row.get("id"), "path.id")
        for field in ("functional", "visual", "accessibility"):
            if field not in row:
                missing = True
            else:
                statuses.append(_status(row[field], "path." + field))
        refs = _list(row.get("evidence_refs"), "path.evidence_refs")
        for ref in refs: _text(ref, "path.evidence_ref")
    content = _obj(record.get("content_checks"), "content_checks")
    for field in ("real_content", "no_explanation_overload", "states_covered"):
        if field not in content:
            missing = True
        elif field == "states_covered":
            states = _list(content[field], "content_checks.states_covered", 2)
            covered = {str(x) for x in states}
            if ui_format == FORMAT["ui_strict"]:
                if not {"empty", "error", "success"}.issubset(covered):
                    missing = True
            elif not covered & {"empty", "error", "success"}:
                missing = True
        else:
            statuses.append(_status(content[field], "content_checks." + field))
    if ui_format == FORMAT["ui_strict"]:
        interactions = _obj(record.get("interaction_checks"), "interaction_checks")
        for field in ("keyboard", "focus", "error_recovery", "unsaved_recovery"):
            if field not in interactions:
                missing = True
            else:
                statuses.append(_status(interactions[field], "interaction_checks." + field))
        refs = _list(interactions.get("evidence_refs"), "interaction_checks.evidence_refs", 4)
        for ref in refs:
            _text(ref, "interaction_checks.evidence_ref")
    copy_ratio = record.get("explanatory_copy_ratio")
    if copy_ratio is not None: _number(copy_ratio, "explanatory_copy_ratio", 0)
    if copy_ratio is not None and copy_ratio > 0.35: statuses.append("INCOMPLETE")
    return {"format": ui_format, "status": _overall(statuses, missing),
            "viewports": len(viewports), "paths": len(paths),
            "explanatory_copy_ratio": copy_ratio,
            "limits": "Evidence contract only; it does not judge taste, brand quality, or production accessibility compliance."}


def validate_system(record):
    record = _obj(record, "record")
    system_format = record.get("format")
    if system_format not in {FORMAT["system"], FORMAT["system_strict"]}:
        _fail("record.format must be " + FORMAT["system"] + " or " + FORMAT["system_strict"])
    _text(record.get("system"), "system")
    traffic = _obj(record.get("traffic_profile"), "traffic_profile")
    for field in ("peak_rps", "burst_rps", "concurrency", "payload_kb"):
        _number(traffic.get(field), "traffic_profile." + field, 0)
    slos = _obj(record.get("slos"), "slos")
    for field in ("availability_target", "latency_p95_ms", "error_rate_target"):
        _number(slos.get(field), "slos." + field, 0)
    capacity = _obj(record.get("capacity_model"), "capacity_model")
    for field in ("instance_capacity_rps", "instances", "headroom_ratio", "expected_concurrency"):
        _number(capacity.get(field), "capacity_model." + field, 0)
    modeled_capacity = capacity["instance_capacity_rps"] * capacity["instances"]
    capacity_ratio = modeled_capacity / max(traffic["burst_rps"], 1)
    modeled_concurrency = traffic["peak_rps"] * slos["latency_p95_ms"] / 1000
    capacity_status = "PASS" if capacity_ratio >= 1.2 and abs(modeled_concurrency - capacity["expected_concurrency"]) / max(modeled_concurrency, 1) <= 0.2 else ("FAIL" if capacity_ratio < 1.0 else "INCOMPLETE")
    controls = _obj(record.get("controls"), "controls")
    required = ("idempotency", "backpressure", "bounded_retries", "authz_audit",
                "migration_rollback", "observability", "load_test", "recovery_drill")
    statuses = [capacity_status]; missing = False
    for field in required:
        if field not in controls: missing = True
        else: statuses.append(_status(controls[field], "controls." + field))
    evidence = _list(record.get("evidence_refs"), "evidence_refs")
    for ref in evidence: _text(ref, "evidence_ref")
    if system_format == FORMAT["system_strict"]:
        load = _obj(record.get("load_evidence"), "load_evidence")
        for field in ("requests", "concurrency", "p95_ms", "error_rate"):
            _number(load.get(field), "load_evidence." + field, 0)
        statuses.append(_status(load.get("status"), "load_evidence.status"))
        _list(load.get("evidence_refs"), "load_evidence.evidence_refs")
        recovery = _obj(record.get("recovery_evidence"), "recovery_evidence")
        _text(recovery.get("drill_id"), "recovery_evidence.drill_id")
        statuses.append(_status(recovery.get("status"), "recovery_evidence.status"))
        statuses.append(_status(recovery.get("rollback_verified"), "recovery_evidence.rollback_verified"))
        _list(recovery.get("evidence_refs"), "recovery_evidence.evidence_refs")
    return {"format": system_format, "status": _overall(statuses, missing),
            "required_controls": list(required), "capacity_ratio": round(capacity_ratio, 3),
            "modeled_concurrency": round(modeled_concurrency, 3), "evidence_refs": evidence,
            "limits": "Design evidence does not prove production throughput; load tests must match the target environment."}


def validate_host(record):
    record = _obj(record, "record")
    if record.get("format") != FORMAT["host"]:
        _fail("record.format must be " + FORMAT["host"])
    for field in ("host_id", "host_version", "platform"): _text(record.get(field), field)
    caps = _obj(record.get("capabilities"), "capabilities")
    required = _list(record.get("required_capabilities"), "required_capabilities")
    statuses = []
    for name in required:
        _text(name, "required_capability")
        value = caps.get(name, "unknown")
        if value == "supported":
            statuses.append("PASS")
        elif value == "unsupported":
            statuses.append("FAIL")
        elif value == "unknown":
            statuses.append("INCOMPLETE")
        else:
            _fail("capabilities." + name + " must be supported/unsupported/unknown")
    phases = _list(record.get("phases"), "phases", 5)
    expected = {"install", "load", "invoke", "route", "verify"}
    if not expected.issubset({str(x) for x in phases}): _fail("phases must include install/load/invoke/route/verify")
    observations = _list(record.get("observations"), "observations")
    observed_phases = {row.get("phase") for row in observations if isinstance(row, dict)}
    missing = expected - observed_phases
    phase_statuses = [_status(row.get("status"), "observation.status") for row in observations if isinstance(row, dict)]
    return {"format": FORMAT["host"], "status": _overall(statuses + phase_statuses, bool(missing)),
            "missing_phases": sorted(missing), "required_capabilities": required,
            "limits": "Host evidence is version-bound; installation does not prove automatic loading or task routing."}


def validate_autonomy(record):
    record = _obj(record, "record")
    autonomy_format = record.get("format")
    if autonomy_format not in {FORMAT["autonomy"], FORMAT["autonomy_strict"]}:
        _fail("record.format must be " + FORMAT["autonomy"] + " or " + FORMAT["autonomy_strict"])
    signals = _list(record.get("signals"), "signals")
    statuses=[]
    for row in signals:
        row=_obj(row,"signal"); _text(row.get("id"),"signal.id"); _text(row.get("source"),"signal.source")
        statuses.append(_status(row.get("status"),"signal.status")); _text(row.get("observed_at"),"signal.observed_at")
    auth=_obj(record.get("authorization"),"authorization")
    _text(auth.get("scope"),"authorization.scope")
    _list(auth.get("allowed_actions"),"authorization.allowed_actions")
    _list(auth.get("forbidden_actions"),"authorization.forbidden_actions")
    action=_obj(record.get("next_action"),"next_action")
    for field in ("id","kind","reason","rollback"): _text(action.get(field),"next_action."+field)
    _number(action.get("estimated_cost"),"next_action.estimated_cost",0)
    _number(record.get("budget"), "budget", 0)
    if action["estimated_cost"] > record["budget"]:
        statuses.append("FAIL")
    stops=_list(record.get("stop_conditions"),"stop_conditions",2)
    recovery=_obj(record.get("recovery"),"recovery")
    for field in ("on_error","unknown_state","external_result_unknown"):
        _text(recovery.get(field),"recovery."+field)
    if autonomy_format == FORMAT["autonomy_strict"]:
        progress = _obj(record.get("progress_ledger"), "progress_ledger")
        _text(progress.get("run_id"), "progress_ledger.run_id")
        _number(progress.get("completed_steps"), "progress_ledger.completed_steps", 0)
        _number(progress.get("max_steps"), "progress_ledger.max_steps", 1)
        if progress["completed_steps"] > progress["max_steps"]: statuses.append("FAIL")
        statuses.append(_status(progress.get("checkpoint_verified"), "progress_ledger.checkpoint_verified"))
        statuses.append(_status(progress.get("resume_verified"), "progress_ledger.resume_verified"))
        _list(progress.get("evidence_refs"), "progress_ledger.evidence_refs")
    return {"format": autonomy_format, "status": _overall(statuses),
            "signal_count": len(signals), "stop_condition_count": len(stops),
            "limits": "This is a bounded next-action contract; it is not a scheduler, daemon, or guarantee of unattended execution."}


def _usage(row):
    usage = row.get("usage") or {}; usage = usage.get("usage") or {}
    return usage.get("total_tokens")


def _is_capacity_failure(row):
    """Return whether a row was censored by provider capacity.

    Evaluation receipts have used both the explicit ``capacity_failure`` flag
    and the normalized ``failure_class`` field.  Treat either representation
    as capacity evidence while leaving all other censored outcomes untouched.
    """
    if row.get("capacity_failure") is True:
        return True
    failure_class = row.get("failure_class")
    return isinstance(failure_class, str) and failure_class.strip().lower() == "capacity_error"


def _supplemental_evidence_plan(path, min_valid_pairs, valid, censored, capacity_tasks):
    """Build an actionable plan without changing the comparison result.

    The plan is descriptive evidence work: it never promotes a censored row to
    a successful pair and it never recommends switching the frozen model.
    """
    additional = max(min_valid_pairs - len(valid), 0)
    capacity_set = set(capacity_tasks)
    other_censored = [task for task in censored if task not in capacity_set]
    steps = []
    if capacity_tasks:
        steps.append({
            "id": "replay_capacity_censored_pairs",
            "action": "retry_same_frozen_protocol_after_bounded_capacity_recovery",
            "tasks": capacity_tasks,
            "acceptance": "Keep each pair censored until both arms are completed and evaluation.task_success is true.",
            "constraints": [
                "persist the original capacity failure receipt",
                "retry the same model, reasoning effort, tools, and task contract",
                "do not switch models or count a capacity failure as success",
            ],
        })
    if other_censored:
        steps.append({
            "id": "replay_non_comparable_pairs",
            "action": "replay_censored_or_failed_pairs_under_the_frozen_protocol",
            "tasks": other_censored,
            "acceptance": "Count a pair only when both arms are completed and evaluation.task_success is true.",
        })
    if additional:
        steps.append({
            "id": "collect_additional_valid_pairs",
            "action": "run_matched_pairs_under_the_frozen_protocol",
            "minimum_new_valid_pairs": additional,
            "acceptance": "Raise valid_completed_pairs to at least min_valid_pairs before interpreting deltas.",
        })
    if steps:
        steps.append({
            "id": "rerun_compare",
            "action": "rerun_compare_after_receipts_are_appended",
            "command": [
                "python",
                "portable-agent-kit/scripts/quality_contract.py",
                "compare",
                "--results",
                str(Path(path)),
                "--min-valid-pairs",
                str(min_valid_pairs),
            ],
            "acceptance": "Recheck status and keep INCOMPLETE whenever the valid sample remains below the threshold.",
        })
    return {
        "required": bool(steps),
        "minimum_valid_pairs": min_valid_pairs,
        "additional_valid_pairs_needed": additional,
        "capacity_censored_tasks": capacity_tasks,
        "steps": steps,
    }


def _cost_usd(row):
    if isinstance(row.get("cost_usd"), (int, float)) and not isinstance(row.get("cost_usd"), bool):
        return float(row["cost_usd"])
    usage = row.get("usage") or {}; usage = usage.get("usage") or {}
    rates = row.get("pricing_usd_per_million_tokens") or {}
    total = usage.get("total_tokens")
    if total is None or not isinstance(rates.get("total"), (int, float)):
        return None
    return float(total) * float(rates["total"]) / 1000000


def compare_results(path, min_valid_pairs=3, require_cost_evidence=False):
    rows=[]
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip(): rows.append(json.loads(line))
    pairs=defaultdict(dict)
    for row in rows:
        if not isinstance(row,dict): _fail("result row must be an object")
        for field in ("task_id","arm","status"): _text(row.get(field),"result."+field)
        if row["arm"] not in {"baseline","solo-1.0"}: _fail("result.arm must be baseline or solo-1.0")
        pairs[row["task_id"]][row["arm"]]=row
    valid=[]; censored=[]; capacity_tasks=[]
    for task, arms in sorted(pairs.items()):
        if set(arms) != {"baseline","solo-1.0"}: _fail("missing arm for task: "+task)
        if all(r.get("status")=="completed" and (r.get("evaluation") or {}).get("task_success")
               and not _is_capacity_failure(r) for r in arms.values()):
            valid.append(task)
        else:
            censored.append(task)
            if any(_is_capacity_failure(row) for row in arms.values()):
                capacity_tasks.append(task)
    eligible=[]
    for task in valid:
        b,s=pairs[task]["baseline"],pairs[task]["solo-1.0"]
        q=lambda r:(r.get("evaluation") or {}).get("quality_score")
        eligible.append({"task_id":task,"quality_delta":q(s)-q(b),
                         "elapsed_delta":s.get("elapsed_seconds")-b.get("elapsed_seconds"),
                         "token_delta":(_usage(s)-_usage(b)) if _usage(s) is not None and _usage(b) is not None else None,
                         "cost_delta_usd":(_cost_usd(s)-_cost_usd(b)) if _cost_usd(s) is not None and _cost_usd(b) is not None else None})
    status="PASS" if len(valid)>=min_valid_pairs else "INCOMPLETE"
    cost_values = [row["cost_delta_usd"] for row in eligible if row["cost_delta_usd"] is not None]
    cost_status = "PASS" if len(cost_values) == len(eligible) and cost_values else ("INCOMPLETE" if require_cost_evidence else "UNMEASURED")
    if require_cost_evidence and cost_status != "PASS": status = "INCOMPLETE"
    return {"format":"ab-compare/1","status":status,"tasks":len(pairs),
            "valid_completed_pairs":len(valid),"censored_or_failed_pairs":len(censored),
            "eligible_pairs":eligible,"cost_evidence_status":cost_status,
            "cost_delta_mean_usd":round(sum(cost_values)/len(cost_values), 8) if cost_values else None,
            "limits":"No superiority claim is allowed below min_valid_pairs, without matched cost evidence, or when capacity/connection failures are censored.",
            "capacity_censored_pairs":len(capacity_tasks),
            "supplemental_evidence_plan":_supplemental_evidence_plan(path, min_valid_pairs, valid, censored, capacity_tasks)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    v=sub.add_parser("validate"); v.add_argument("--kind",choices=tuple(FORMAT)); v.add_argument("--input",type=Path,required=True)
    c=sub.add_parser("compare"); c.add_argument("--results",type=Path,required=True); c.add_argument("--min-valid-pairs",type=int,default=3); c.add_argument("--require-cost-evidence",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="validate":
            record=json.loads(args.input.read_text(encoding="utf-8-sig")); result={"ui":validate_ui,"ui_strict":validate_ui,"system":validate_system,"system_strict":validate_system,"host":validate_host,"autonomy":validate_autonomy,"autonomy_strict":validate_autonomy}[args.kind](record)
        else: result=compare_results(args.results,args.min_valid_pairs,args.require_cost_evidence)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result={"status":"INVALID","error":str(error)}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get("status")=="PASS" else 2

if __name__=="__main__": raise SystemExit(main())
