#!/usr/bin/env python3
"""Validate arm-blind review packets bound to a frozen 0.13 evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))
import eval_contract

VERSION = "0.1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERDICTS = {"PASS", "FAIL", "UNKNOWN"}


class ReviewError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReviewError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError("cannot read JSON %s: %s" % (path, exc)) from exc


def load_jsonl(path, label):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReviewError("cannot read %s: %s" % (label, exc)) from exc
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError("%s line %d is not JSON: %s" % (label, number, exc)) from exc
        require(isinstance(value, dict), "%s line %d must be an object" % (label, number))
        rows.append(value)
    return rows


def _task_by_id(contract):
    return {task["id"]: task for task in contract["tasks"]}


def _receipt_evidence(receipt, line_label):
    refs = receipt.get("evidence_refs")
    require(isinstance(refs, list) and refs, "%s needs evidence_refs" % line_label)
    result = {}
    for index, ref in enumerate(refs):
        require(isinstance(ref, dict), "%s evidence_refs[%d] must be an object" % (line_label, index))
        for key in ("id", "sha256", "trial_id", "contract_id", "source_revision", "candidate_sha256"):
            require(isinstance(ref.get(key), str) and ref[key], "%s evidence_refs[%d] missing %s" % (line_label, index, key))
        require(HEX64.fullmatch(ref["sha256"]) is not None, "%s evidence_refs[%d] sha256 invalid" % (line_label, index))
        require(HEX64.fullmatch(ref["candidate_sha256"]) is not None, "%s evidence_refs[%d] candidate_sha256 invalid" % (line_label, index))
        require(ref["id"] not in result, "%s evidence id duplicated: %s" % (line_label, ref["id"]))
        require(ref["trial_id"] == receipt["trial_id"], "%s evidence trial mismatch" % line_label)
        require(ref["contract_id"] == receipt["contract_id"], "%s evidence contract mismatch" % line_label)
        require(ref["source_revision"] == receipt["source_revision"], "%s evidence source revision mismatch" % line_label)
        require(ref["candidate_sha256"] == receipt["candidate_sha256"], "%s evidence candidate mismatch" % line_label)
        result[ref["id"]] = ref
    return result


def validate_receipts_for_review(contract, contract_path, receipt_path):
    """Apply the normal receipt checks plus strict version/evidence binding."""
    eval_contract.validate_contract(contract)
    rows = eval_contract.validate_receipts(contract, receipt_path, contract_path=contract_path)
    contract_sha = digest_file(contract_path)
    by_trial = {}
    for index, row in enumerate(rows, 1):
        label = "receipt %d" % index
        require(row.get("contract_sha256") == contract_sha, "%s contract_sha256 must bind the supplied contract" % label)
        require(isinstance(row.get("source_revision"), str) and row["source_revision"], "%s source_revision required" % label)
        require(HEX64.fullmatch(row.get("candidate_sha256", "")) is not None, "%s candidate_sha256 invalid" % label)
        _receipt_evidence(row, label)
        by_trial[row["trial_id"]] = row
    return by_trial


def _review_observations(contract, receipt, row, label):
    task = _task_by_id(contract)[receipt["task_id"]]
    observations = row.get("observations")
    require(isinstance(observations, dict), "%s observations must be an object" % label)
    expected = {obs["id"] for obs in task["expected_observations"]}
    require(set(observations) == expected, "%s observations must exactly match expected observation ids" % label)
    allowed_evidence = set(_receipt_evidence(receipt, label + " receipt"))
    for obs_id in sorted(expected):
        item = observations[obs_id]
        require(isinstance(item, dict), "%s observation %s must be an object" % (label, obs_id))
        require(item.get("verdict") in VERDICTS, "%s observation %s verdict invalid" % (label, obs_id))
        refs = item.get("evidence_refs", [])
        require(isinstance(refs, list), "%s observation %s evidence_refs invalid" % (label, obs_id))
        require(all(isinstance(ref, str) and ref in allowed_evidence for ref in refs), "%s observation %s references unbound evidence" % (label, obs_id))
        if item["verdict"] in {"PASS", "FAIL"}:
            require(refs, "%s observation %s %s needs evidence" % (label, obs_id, item["verdict"]))
        if item["verdict"] == "UNKNOWN":
            require(isinstance(item.get("reason"), str) and item["reason"].strip(), "%s observation %s UNKNOWN needs reason" % (label, obs_id))


def validate_reviews(contract, contract_path, receipt_path, review_path):
    receipts = validate_receipts_for_review(contract, contract_path, receipt_path)
    tasks = _task_by_id(contract)
    reviews = load_jsonl(review_path, "reviews")
    seen = set()
    output = []
    for index, row in enumerate(reviews, 1):
        label = "review %d" % index
        require(row.get("schema_version") == VERSION, "%s schema_version mismatch" % label)
        require(row.get("contract_id") == contract["contract_id"], "%s contract mismatch" % label)
        require(isinstance(row.get("packet_id"), str) and row["packet_id"], "%s packet_id required" % label)
        require(row["packet_id"] not in seen, "%s packet_id duplicated" % label)
        seen.add(row["packet_id"])
        require(isinstance(row.get("trial_id"), str) and row["trial_id"] in receipts, "%s trial_id unknown" % label)
        receipt = receipts[row["trial_id"]]
        require(row.get("task_id") == receipt["task_id"], "%s task mismatch" % label)
        require(row.get("repeat") == receipt["repeat"], "%s repeat mismatch" % label)
        require(row.get("receipt_sha256") == digest_value(receipt), "%s receipt_sha256 mismatch" % label)
        require(isinstance(row.get("reviewer_id"), str) and row["reviewer_id"].strip(), "%s reviewer_id required" % label)
        require(row.get("blind") is True, "%s blind must be true" % label)
        require("arm_id" not in row and "source_revision" not in row and "candidate_sha256" not in row, "%s leaks arm/source identity" % label)
        require(row.get("evaluator_sha256") == contract.get("freeze", {}).get("evaluator_sha256"), "%s evaluator hash mismatch" % label)
        require(row.get("final_verdict") in VERDICTS, "%s final_verdict invalid" % label)
        _review_observations(contract, receipt, row, label)
        if row["final_verdict"] == "PASS":
            require(all(item["verdict"] == "PASS" for item in row["observations"].values()), "%s PASS requires all observations PASS" % label)
        output.append(row)
    expected = {(task_id, arm_id, repeat) for task_id in tasks for arm_id in {r["arm_id"] for r in receipts.values()} for repeat in range(1, contract["repetitions"] + 1)}
    observed = {(row["task_id"], receipts[row["trial_id"]]["arm_id"], row["repeat"]) for row in output}
    missing = sorted(expected - observed)
    return {"status": "COMPLETE" if not missing else "INCOMPLETE", "reviews": len(output), "expected_reviews": len(expected), "missing_reviews": [{"task_id": t, "arm_id": a, "repeat": r} for t, a, r in missing]}


def make_packets(contract, contract_path, receipt_path):
    receipts = validate_receipts_for_review(contract, contract_path, receipt_path)
    tasks = _task_by_id(contract)
    contract_sha = digest_file(contract_path)
    packets, mapping = [], []
    for trial_id, receipt in sorted(receipts.items()):
        packet_id = "packet-" + hashlib.sha256((contract_sha + trial_id).encode("utf-8")).hexdigest()[:20]
        task = tasks[receipt["task_id"]]
        refs = _receipt_evidence(receipt, "receipt %s" % trial_id)
        packets.append({
            "schema_version": VERSION,
            "packet_id": packet_id,
            "contract_id": contract["contract_id"],
            "task_id": receipt["task_id"],
            "repeat": receipt["repeat"],
            "objective": task["objective"],
            "expected_observations": task["expected_observations"],
            "receipt_sha256": digest_value(receipt),
            "evidence": [{"id": ref_id, "sha256": ref["sha256"]} for ref_id, ref in sorted(refs.items())],
            "blind": True,
        })
        mapping.append({"packet_id": packet_id, "trial_id": trial_id, "arm_id": receipt["arm_id"], "source_revision": receipt["source_revision"]})
    return packets, mapping


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate")
    v.add_argument("contract", type=Path); v.add_argument("receipts", type=Path); v.add_argument("reviews", type=Path)
    v.add_argument("--allow-incomplete", action="store_true")
    p = sub.add_parser("make-packets")
    p.add_argument("contract", type=Path); p.add_argument("receipts", type=Path); p.add_argument("output", type=Path); p.add_argument("--mapping", type=Path)
    args = parser.parse_args(argv)
    try:
        contract = load_json(args.contract)
        if args.command == "validate":
            result = validate_reviews(contract, args.contract, args.receipts, args.reviews)
            if result["status"] == "INCOMPLETE" and not args.allow_incomplete:
                print(json.dumps(result, ensure_ascii=False, indent=2)); return 2
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        packets, mapping = make_packets(contract, args.contract, args.receipts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in packets), encoding="utf-8")
        if args.mapping:
            args.mapping.parent.mkdir(parents=True, exist_ok=True)
            args.mapping.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in mapping), encoding="utf-8")
        print(json.dumps({"status": "PACKETS_WRITTEN", "packets": len(packets), "output": str(args.output)}, ensure_ascii=False, indent=2)); return 0
    except (ReviewError, eval_contract.ContractError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
