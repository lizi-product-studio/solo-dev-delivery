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
    "system": "system-design/1",
    "host": "host-adapter/1",
    "autonomy": "autonomy-cycle/1",
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
    if record.get("format") != FORMAT["ui"]:
        _fail("record.format must be " + FORMAT["ui"])
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
            if not {str(x) for x in states} & {"empty", "error", "success"}:
                missing = True
        else:
            statuses.append(_status(content[field], "content_checks." + field))
    copy_ratio = record.get("explanatory_copy_ratio")
    if copy_ratio is not None: _number(copy_ratio, "explanatory_copy_ratio", 0)
    if copy_ratio is not None and copy_ratio > 0.35: statuses.append("INCOMPLETE")
    return {"format": FORMAT["ui"], "status": _overall(statuses, missing),
            "viewports": len(viewports), "paths": len(paths),
            "explanatory_copy_ratio": copy_ratio,
            "limits": "Evidence contract only; it does not judge taste, brand quality, or production accessibility compliance."}


def validate_system(record):
    record = _obj(record, "record")
    if record.get("format") != FORMAT["system"]:
        _fail("record.format must be " + FORMAT["system"])
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
    return {"format": FORMAT["system"], "status": _overall(statuses, missing),
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
    if record.get("format") != FORMAT["autonomy"]:
        _fail("record.format must be " + FORMAT["autonomy"])
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
    return {"format": FORMAT["autonomy"], "status": _overall(statuses),
            "signal_count": len(signals), "stop_condition_count": len(stops),
            "limits": "This is a bounded next-action contract; it is not a scheduler, daemon, or guarantee of unattended execution."}


def _usage(row):
    usage = row.get("usage") or {}; usage = usage.get("usage") or {}
    return usage.get("total_tokens")


def compare_results(path, min_valid_pairs=3):
    rows=[]
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip(): rows.append(json.loads(line))
    pairs=defaultdict(dict)
    for row in rows:
        if not isinstance(row,dict): _fail("result row must be an object")
        for field in ("task_id","arm","status"): _text(row.get(field),"result."+field)
        if row["arm"] not in {"baseline","solo-1.0"}: _fail("result.arm must be baseline or solo-1.0")
        pairs[row["task_id"]][row["arm"]]=row
    valid=[]; censored=[]
    for task, arms in sorted(pairs.items()):
        if set(arms) != {"baseline","solo-1.0"}: _fail("missing arm for task: "+task)
        if all(r.get("status")=="completed" and (r.get("evaluation") or {}).get("task_success") for r in arms.values()):
            valid.append(task)
        else: censored.append(task)
    eligible=[]
    for task in valid:
        b,s=pairs[task]["baseline"],pairs[task]["solo-1.0"]
        q=lambda r:(r.get("evaluation") or {}).get("quality_score")
        eligible.append({"task_id":task,"quality_delta":q(s)-q(b),
                         "elapsed_delta":s.get("elapsed_seconds")-b.get("elapsed_seconds"),
                         "token_delta":(_usage(s)-_usage(b)) if _usage(s) is not None and _usage(b) is not None else None})
    status="PASS" if len(valid)>=min_valid_pairs else "INCOMPLETE"
    return {"format":"ab-compare/1","status":status,"tasks":len(pairs),
            "valid_completed_pairs":len(valid),"censored_or_failed_pairs":len(censored),
            "eligible_pairs":eligible,"limits":"No superiority claim is allowed below min_valid_pairs or when capacity/connection failures are censored."}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    v=sub.add_parser("validate"); v.add_argument("--kind",choices=tuple(FORMAT)); v.add_argument("--input",type=Path,required=True)
    c=sub.add_parser("compare"); c.add_argument("--results",type=Path,required=True); c.add_argument("--min-valid-pairs",type=int,default=3)
    args=parser.parse_args(argv)
    try:
        if args.command=="validate":
            record=json.loads(args.input.read_text(encoding="utf-8-sig")); result={"ui":validate_ui,"system":validate_system,"host":validate_host,"autonomy":validate_autonomy}[args.kind](record)
        else: result=compare_results(args.results,args.min_valid_pairs)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result={"status":"INVALID","error":str(error)}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get("status")=="PASS" else 2

if __name__=="__main__": raise SystemExit(main())
