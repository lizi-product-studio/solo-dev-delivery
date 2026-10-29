"""Evidence-bound, bounded self-improvement decisions.

This module coordinates an upgrade candidate; it does not mutate source,
publish a release, schedule a worker, change permissions, or retry external
work.  Promotion requires receipts that are bound to the same proposal and
candidate, so callers cannot turn three strings saying PASS into a release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "self-improvement/2"
CLASSES = ("requirement", "tool", "permission", "capacity", "transport",
           "acceptance", "cost", "unknown")
STATUSES = ("success", "failure", "unknown")
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


class LearningError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise LearningError(name + " must be a non-empty string")
    return value.strip()


def _list(value, name, *, allow_empty=False):
    if not isinstance(value, list):
        raise LearningError(name + " must be a list")
    if not allow_empty and not value:
        raise LearningError(name + " must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise LearningError(name + " must contain non-empty strings")
    return [item.strip() for item in value]


def _digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence_refs(value, evidence_root=None, name="evidence_refs"):
    if not isinstance(value, list) or not value:
        raise LearningError(name + " must be a non-empty list")
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            # Legacy ledgers are retained as incomplete evidence.  They may be
            # read for history but cannot satisfy a proposal's evidence gate.
            result.append({"uri": item.strip(), "sha256": None, "legacy": True})
            continue
        if not isinstance(item, dict):
            raise LearningError("%s[%d] must be an object" % (name, index))
        uri = _text(item.get("uri"), "%s[%d].uri" % (name, index))
        sha = item.get("sha256")
        if not isinstance(sha, str) or _HASH.fullmatch(sha) is None:
            raise LearningError("%s[%d].sha256 must be a SHA-256 digest" % (name, index))
        clean = {"uri": uri, "sha256": sha.lower()}
        if "kind" in item:
            clean["kind"] = _text(item["kind"], "%s[%d].kind" % (name, index))
        if evidence_root is not None:
            root = Path(evidence_root).resolve()
            path = (root / uri).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                raise LearningError("evidence URI escapes evidence_root") from None
            if not path.is_file() or _digest_file(path) != clean["sha256"]:
                raise LearningError("evidence hash does not match: " + uri)
        result.append(clean)
    return result


def normalize_record(record, *, evidence_root=None):
    if not isinstance(record, dict):
        raise LearningError("experience record must be an object")
    schema = record.get("schema", FORMAT)
    if schema not in {FORMAT, "self-improvement/1"}:
        raise LearningError("unsupported experience schema")
    status = _text(record.get("status"), "status").lower()
    if status not in STATUSES:
        raise LearningError("status must be success, failure or unknown")
    result = {
        "schema": FORMAT,
        "source_schema": schema,
        "run_id": _text(record.get("run_id"), "run_id"),
        "task_id": _text(record.get("task_id"), "task_id"),
        "candidate": _text(record.get("candidate"), "candidate"),
        "status": status,
        "observed_at": _text(record.get("observed_at", datetime.now(timezone.utc).isoformat()),
                              "observed_at"),
        "evidence_refs": _evidence_refs(record.get("evidence_refs"), evidence_root),
    }
    if status == "failure":
        failure_class = _text(record.get("failure_class"), "failure_class").lower()
        if failure_class not in CLASSES:
            raise LearningError("failure_class is not supported")
        result["failure_class"] = failure_class
        result["root_cause"] = _text(record.get("root_cause", failure_class), "root_cause")
        result["message"] = _text(record.get("message"), "message")
    elif record.get("failure_class") is not None:
        failure_class = _text(record.get("failure_class"), "failure_class").lower()
        if failure_class not in CLASSES:
            raise LearningError("failure_class is not supported")
        result["failure_class"] = failure_class
    if "cost_usd" in record:
        value = record["cost_usd"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise LearningError("cost_usd must be a number >= 0")
        result["cost_usd"] = float(value)
    return result


def append_record(path, record, *, evidence_root=None):
    normalized = normalize_record(record, evidence_root=evidence_root)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
    return normalized


def read_ledger(path, *, evidence_root=None):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(normalize_record(json.loads(line), evidence_root=evidence_root))
        except (json.JSONDecodeError, LearningError) as exc:
            raise LearningError("ledger row %d: %s" % (number, exc)) from None
    return rows


def propose(path, candidate, change_summary, patch_refs, *, min_failures=2,
            evidence_root=None):
    if isinstance(min_failures, bool) or not isinstance(min_failures, int) or min_failures < 2:
        raise LearningError("min_failures must be an integer >= 2")
    rows = read_ledger(path, evidence_root=evidence_root)
    groups = defaultdict(set)
    examples = defaultdict(list)
    for row in rows:
        refs = row["evidence_refs"]
        if row["status"] != "failure" or any(ref.get("sha256") is None for ref in refs):
            continue
        key = (row["task_id"], row.get("failure_class", "unknown"),
               row.get("root_cause", "unknown"), row["candidate"])
        groups[key].add(row["run_id"])
        examples[key].append(row)
    targets = []
    for (task_id, failure_class, root_cause, source_candidate), run_ids in sorted(groups.items()):
        if len(run_ids) >= min_failures:
            targets.append({
                "task_id": task_id,
                "source_candidate": source_candidate,
                "failure_class": failure_class,
                "root_cause": root_cause,
                "distinct_runs": sorted(run_ids),
                "observed_failures": len(run_ids),
                "evidence_refs": [ref for row in examples[(task_id, failure_class, root_cause, source_candidate)]
                                  for ref in row["evidence_refs"]],
            })
    return {
        "format": FORMAT,
        "proposal_id": "upgrade-" + uuid.uuid4().hex,
        "candidate": _text(candidate, "candidate"),
        "based_on_runs": sorted({row["run_id"] for row in rows}),
        "targets": targets,
        "change_summary": _text(change_summary, "change_summary"),
        "patch_refs": _list(patch_refs, "patch_refs"),
        "status": "PROPOSED" if targets else "INSUFFICIENT_EVIDENCE",
        "gates_required": ["independent_review", "regression", "canary", "rollback_plan"],
        "limits": "Proposal only; source mutation and publication require separately authorized host actions.",
    }


def _receipt(path, name, proposal):
    target = Path(path)
    if not target.is_file():
        raise LearningError(name + " receipt is missing")
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LearningError(name + " receipt is unreadable: " + str(exc)) from None
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise LearningError(name + " receipt must be PASS")
    if value.get("proposal_id") != proposal.get("proposal_id"):
        raise LearningError(name + " receipt proposal binding mismatch")
    if value.get("candidate") != proposal.get("candidate"):
        raise LearningError(name + " receipt candidate binding mismatch")
    refs = value.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str)
            or _HASH.fullmatch(ref["sha256"]) is None for ref in refs):
        raise LearningError(name + " receipt needs hashed evidence_refs")
    return value


def gate(proposal, independent_review, regression, canary, rollback_plan):
    if not isinstance(proposal, dict) or proposal.get("format") != FORMAT:
        raise LearningError("proposal must use " + FORMAT)
    if proposal.get("status") != "PROPOSED":
        return {"format": FORMAT, "status": "BLOCKED",
                "reason": "proposal has no repeated, hashed failure evidence"}
    receipts = {
        "independent_review": _receipt(independent_review, "independent_review", proposal),
        "regression": _receipt(regression, "regression", proposal),
        "canary": _receipt(canary, "canary", proposal),
    }
    rollback = _receipt(rollback_plan, "rollback_plan", proposal)
    return {
        "format": FORMAT,
        "status": "RELEASE_CANDIDATE",
        "proposal_id": proposal["proposal_id"],
        "candidate": proposal["candidate"],
        "checks": {name: value["status"] for name, value in receipts.items()},
        "receipt_refs": {name: str(Path(path).resolve()) for name, path in (
            ("independent_review", independent_review), ("regression", regression),
            ("canary", canary), ("rollback_plan", rollback_plan))},
        "rollback_plan_status": rollback["status"],
        "limits": "Release candidate only; host must activate and read back the candidate separately.",
    }


def rollback(candidate, reason, evidence_ref, *, proposal_id=None):
    result = {"format": FORMAT, "status": "ROLLED_BACK",
              "candidate": _text(candidate, "candidate"),
              "reason": _text(reason, "reason"),
              "evidence_ref": _text(evidence_ref, "evidence_ref"),
              "readback_required": True}
    if proposal_id is not None:
        result["proposal_id"] = _text(proposal_id, "proposal_id")
    return result


def wake(run_id, budget, steps_used, max_steps, authorization_scope,
         allowed_actions, forbidden_actions, external_result_unknown=False):
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise LearningError("budget must be an integer >= 0")
    if isinstance(steps_used, bool) or not isinstance(steps_used, int) or steps_used < 0:
        raise LearningError("steps_used must be an integer >= 0")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise LearningError("max_steps must be an integer >= 1")
    allowed = set(_list(allowed_actions, "allowed_actions"))
    forbidden = set(_list(forbidden_actions, "forbidden_actions"))
    if allowed & forbidden:
        raise LearningError("allowed_actions and forbidden_actions overlap")
    reason = None
    if budget == 0:
        reason = "budget_exhausted"
    elif steps_used >= max_steps:
        reason = "max_steps_reached"
    elif external_result_unknown:
        reason = "external_result_unknown"
    elif not allowed:
        reason = "authorization_missing"
    status = "PAUSED" if reason else "READY"
    return {
        "format": FORMAT,
        "status": status,
        "run_id": _text(run_id, "run_id"),
        "authorization": {"scope": _text(authorization_scope, "authorization_scope"),
                           "allowed_actions": sorted(allowed),
                           "forbidden_actions": sorted(forbidden)},
        "budget": {"remaining": budget, "steps_used": steps_used, "max_steps": max_steps},
        "next_action": None if reason else "COLLECT_AND_CLASSIFY",
        "stop_reason": reason,
        "limits": "One bounded decision; host owns scheduling, execution, persistence and reconciliation.",
    }


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("--ledger", type=Path, required=True)
    record.add_argument("--input", type=Path, required=True)
    record.add_argument("--evidence-root", type=Path)
    classify = sub.add_parser("classify")
    classify.add_argument("--message", required=True)
    proposal = sub.add_parser("propose")
    proposal.add_argument("--ledger", type=Path, required=True)
    proposal.add_argument("--candidate", required=True)
    proposal.add_argument("--change-summary", required=True)
    proposal.add_argument("--patch-ref", action="append", required=True)
    proposal.add_argument("--min-failures", type=int, default=2)
    proposal.add_argument("--evidence-root", type=Path)
    proposal.add_argument("--output", type=Path, required=True)
    release = sub.add_parser("gate")
    release.add_argument("--proposal", type=Path, required=True)
    release.add_argument("--independent-review", type=Path, required=True)
    release.add_argument("--regression", type=Path, required=True)
    release.add_argument("--canary", type=Path, required=True)
    release.add_argument("--rollback-plan", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)
    revert = sub.add_parser("rollback")
    revert.add_argument("--candidate", required=True)
    revert.add_argument("--reason", required=True)
    revert.add_argument("--evidence-ref", required=True)
    revert.add_argument("--proposal-id")
    revert.add_argument("--output", type=Path, required=True)
    wake_parser = sub.add_parser("wake")
    wake_parser.add_argument("--run-id", required=True)
    wake_parser.add_argument("--budget", type=int, required=True)
    wake_parser.add_argument("--steps-used", type=int, required=True)
    wake_parser.add_argument("--max-steps", type=int, required=True)
    wake_parser.add_argument("--authorization-scope", required=True)
    wake_parser.add_argument("--allow", action="append", dest="allowed_actions", required=True)
    wake_parser.add_argument("--forbid", action="append", dest="forbidden_actions", required=True)
    wake_parser.add_argument("--external-result-unknown", action="store_true")
    wake_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            result = append_record(args.ledger, _read_json(args.input), evidence_root=args.evidence_root)
        elif args.command == "classify":
            text = args.message.lower()
            result = {"failure_class": "capacity" if "capacity" in text else "unknown",
                      "confidence": "heuristic" if "capacity" in text else "unknown"}
        elif args.command == "propose":
            result = propose(args.ledger, args.candidate, args.change_summary, args.patch_ref,
                             min_failures=args.min_failures, evidence_root=args.evidence_root)
        elif args.command == "gate":
            result = gate(_read_json(args.proposal), args.independent_review, args.regression,
                          args.canary, args.rollback_plan)
        elif args.command == "rollback":
            result = rollback(args.candidate, args.reason, args.evidence_ref,
                              proposal_id=args.proposal_id)
        else:
            result = wake(args.run_id, args.budget, args.steps_used, args.max_steps,
                          args.authorization_scope, args.allowed_actions,
                          args.forbidden_actions, args.external_result_unknown)
        if hasattr(args, "output"):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"PROPOSED", "RELEASE_CANDIDATE", "ROLLED_BACK", "READY"} or args.command in {"record", "classify"} else 2
    except (OSError, UnicodeError, ValueError, LearningError, json.JSONDecodeError) as exc:
        print(json.dumps({"format": FORMAT, "status": "INVALID", "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
