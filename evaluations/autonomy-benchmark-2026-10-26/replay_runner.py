#!/usr/bin/env python3
"""Prepare and verify real replay receipts for the frozen autonomy pilot.

This tool deliberately does not invoke a model and never fabricates a result.
``plan`` only emits the frozen trial matrix. ``validate`` accepts receipts from
an actual host/operator run and checks their provenance, observations and
evidence files before the normal contract evaluator summarizes them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2] / "portable-agent-kit"
sys.path.insert(0, str(KIT / "scripts"))
import eval_contract  # type: ignore  # noqa: E402


class ReplayError(ValueError):
    pass


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayError(f"cannot read JSON {path}: {exc}") from exc


def contract_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trial_id(task_id: str, arm_id: str, repeat: int) -> str:
    return f"{task_id}__{arm_id}__r{repeat}"


def matrix(contract: dict):
    return [
        {
            "trial_id": trial_id(task["id"], arm["id"], repeat),
            "schema_version": contract["schema_version"],
            "contract_id": contract["contract_id"],
            "contract_sha256": None,
            "task_id": task["id"],
            "arm_id": arm["id"],
            "repeat": repeat,
            "objective": task["objective"],
            "risk": task["risk"],
            "required_capabilities": task.get("required_capabilities", []),
            "expected_observations": task["expected_observations"],
            "stop_conditions": task.get("stop_conditions", []),
            "status": "planned",
        }
        for task in contract["tasks"]
        for arm in contract["arms"]
        for repeat in range(1, contract["repetitions"] + 1)
    ]


def safe_ref(value: object, evidence_root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ReplayError("evidence reference must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReplayError(f"evidence reference escapes evidence root: {value}")
    resolved = (evidence_root / candidate).resolve()
    root = evidence_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReplayError(f"evidence reference escapes evidence root: {value}") from exc
    if not resolved.is_file():
        raise ReplayError(f"evidence file is missing: {value}")
    return resolved


def validate_observations(row: dict, task: dict, evidence_root: Path) -> None:
    observations = row.get("observations")
    if not isinstance(observations, dict):
        raise ReplayError(f"{row['trial_id']} observations must be an object")
    expected = {item["id"] for item in task["expected_observations"]}
    if set(observations) != expected:
        raise ReplayError(
            f"{row['trial_id']} observations must exactly match expected ids"
        )
    for observation_id, observation in observations.items():
        if not isinstance(observation, dict):
            raise ReplayError(f"{row['trial_id']} observation {observation_id} invalid")
        if observation.get("status") not in {"observed", "not_observed", "unknown"}:
            raise ReplayError(f"{row['trial_id']} observation {observation_id} status invalid")
        refs = observation.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ReplayError(f"{row['trial_id']} observation {observation_id} needs evidence_refs")
        for ref in refs:
            safe_ref(ref, evidence_root)


def validate_provenance(row: dict, task: dict, evidence_root: Path, allow_synthetic: bool) -> None:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        raise ReplayError(f"{row['trial_id']} provenance is required")
    mode = provenance.get("mode")
    if mode not in {"native_agent", "operator_replay", "synthetic_fixture"}:
        raise ReplayError(f"{row['trial_id']} provenance.mode invalid")
    if mode == "synthetic_fixture" and not allow_synthetic:
        raise ReplayError(f"{row['trial_id']} synthetic fixture is refused by default")
    for field in ("host", "skill_revision", "started_at", "ended_at", "run_id"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise ReplayError(f"{row['trial_id']} provenance.{field} is required")
    if provenance.get("task_objective") != task["objective"]:
        raise ReplayError(f"{row['trial_id']} provenance task objective drift")
    refs = row.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ReplayError(f"{row['trial_id']} needs top-level evidence_refs")
    for ref in refs:
        safe_ref(ref, evidence_root)
    digests = provenance.get("evidence_sha256", {})
    if digests is not None:
        if not isinstance(digests, dict):
            raise ReplayError(f"{row['trial_id']} provenance.evidence_sha256 invalid")
        for ref in refs:
            if ref in digests:
                if digests[ref] != hashlib.sha256(safe_ref(ref, evidence_root).read_bytes()).hexdigest():
                    raise ReplayError(f"{row['trial_id']} evidence digest mismatch: {ref}")


def validate_receipts(contract_path: Path, receipts_path: Path, evidence_root: Path, allow_synthetic: bool = False):
    contract = load(contract_path)
    eval_contract.validate_contract(contract)
    rows = eval_contract.validate_receipts(contract, receipts_path)
    tasks = {task["id"]: task for task in contract["tasks"]}
    expected = {entry["trial_id"]: entry for entry in matrix(contract)}
    seen = set()
    for row in rows:
        expected_row = expected.get(row["trial_id"])
        if expected_row is None:
            raise ReplayError(f"unknown trial_id {row['trial_id']}")
        if row["task_id"] != expected_row["task_id"] or row["arm_id"] != expected_row["arm_id"] or row["repeat"] != expected_row["repeat"]:
            raise ReplayError(f"{row['trial_id']} trial identity does not match frozen matrix")
        if row.get("contract_sha256") not in (None, contract_sha(contract_path)):
            raise ReplayError(f"{row['trial_id']} contract hash drift")
        validate_provenance(row, tasks[row["task_id"]], evidence_root, allow_synthetic)
        validate_observations(row, tasks[row["task_id"]], evidence_root)
        seen.add(row["trial_id"])
    missing = sorted(set(expected) - seen)
    summary = eval_contract.summarize(contract, rows)
    summary["missing_trials"] = missing
    summary["provenance_status"] = "VALID"
    return summary


def cmd_plan(args) -> int:
    contract = load(args.contract)
    eval_contract.validate_contract(contract)
    digest = contract_sha(args.contract)
    lines = []
    for row in matrix(contract):
        row["contract_sha256"] = digest
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PLANNED", "trials": len(lines), "output": str(args.output)}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("contract", type=Path)
    plan.add_argument("output", type=Path)
    check = sub.add_parser("validate")
    check.add_argument("contract", type=Path)
    check.add_argument("receipts", type=Path)
    check.add_argument("--evidence-root", type=Path, required=True)
    check.add_argument("--allow-synthetic", action="store_true")
    check.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return cmd_plan(args)
        result = validate_receipts(args.contract, args.receipts, args.evidence_root, args.allow_synthetic)
        if args.require_complete and result["status"] != "COMPLETE":
            result["provenance_status"] = "INCOMPLETE"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReplayError, eval_contract.ContractError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
