#!/usr/bin/env python3
"""Summarize bounded load observations without claiming production capacity.

The input is a JSONL stream of measured request observations.  This tool
computes percentiles and error rate, then compares them with the declared
acceptance thresholds.  A PASS requires enough observations, a concurrency
value, and explicit latency/error thresholds; missing evidence stays
INCOMPLETE.  It never generates traffic or infers capacity from a design.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


FORMAT = "load-evidence/1"


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{name} must be a number >= {minimum}")
    return float(value)


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _percentile(values, fraction):
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * fraction))
    return ordered[rank - 1]


def _read_rows(path):
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"row {line_number} is not valid JSON: {exc}") from None
        if not isinstance(row, dict):
            raise ValueError(f"row {line_number} must be an object")
        elapsed = row.get("elapsed_ms", row.get("latency_ms"))
        elapsed = _number(elapsed, f"row {line_number}.elapsed_ms")
        status = row.get("status")
        if status not in {"success", "error"}:
            raise ValueError(f"row {line_number}.status must be success or error")
        rows.append({"elapsed_ms": elapsed, "status": status})
    return rows


def summarize(rows, *, concurrency=None, p95_target_ms=None, max_error_rate=None,
              min_requests=1, window_seconds=None):
    """Return a deterministic report for already-collected observations."""
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    minimum = _positive_int(min_requests, "min_requests")
    if concurrency is not None:
        concurrency = _positive_int(concurrency, "concurrency")
    if p95_target_ms is not None:
        p95_target_ms = _number(p95_target_ms, "p95_target_ms")
    if max_error_rate is not None:
        max_error_rate = _number(max_error_rate, "max_error_rate", 0)
        if max_error_rate > 1:
            raise ValueError("max_error_rate must be <= 1")
    if window_seconds is not None:
        window_seconds = _number(window_seconds, "window_seconds")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")

    values = []
    errors = 0
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"rows[{index}] must be an object")
        values.append(_number(row.get("elapsed_ms"), f"rows[{index}].elapsed_ms"))
        status = row.get("status")
        if status not in {"success", "error"}:
            raise ValueError(f"rows[{index}].status must be success or error")
        errors += status == "error"

    count = len(values)
    error_rate = (errors / count) if count else None
    p50 = _percentile(values, 0.50) if values else None
    p95 = _percentile(values, 0.95) if values else None
    max_ms = max(values) if values else None
    missing = []
    if count < minimum:
        missing.append("minimum_requests")
    if concurrency is None:
        missing.append("concurrency")
    if p95_target_ms is None:
        missing.append("p95_target_ms")
    if max_error_rate is None:
        missing.append("max_error_rate")

    violations = []
    if p95 is not None and p95_target_ms is not None and p95 > p95_target_ms:
        violations.append("p95_latency")
    if error_rate is not None and max_error_rate is not None and error_rate > max_error_rate:
        violations.append("error_rate")
    if violations:
        status = "FAIL"
    elif missing:
        status = "INCOMPLETE"
    else:
        status = "PASS"

    result = {
        "format": FORMAT,
        "status": status,
        "requests": count,
        "errors": errors,
        "error_rate": round(error_rate, 6) if error_rate is not None else None,
        "p50_ms": round(p50, 3) if p50 is not None else None,
        "p95_ms": round(p95, 3) if p95 is not None else None,
        "max_ms": round(max_ms, 3) if max_ms is not None else None,
        "concurrency": concurrency,
        "window_seconds": window_seconds,
        "throughput_rps": round(count / window_seconds, 3) if window_seconds else None,
        "thresholds": {
            "min_requests": minimum,
            "p95_target_ms": p95_target_ms,
            "max_error_rate": max_error_rate,
        },
        "missing_evidence": missing,
        "violations": violations,
        "limits": "Observed-load summary only; it does not prove production capacity, database behavior, or recovery under the target environment.",
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSONL request observations")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--p95-target-ms", type=float)
    parser.add_argument("--max-error-rate", type=float)
    parser.add_argument("--min-requests", type=int, default=1)
    parser.add_argument("--window-seconds", type=float)
    args = parser.parse_args(argv)
    try:
        result = summarize(
            _read_rows(args.input), concurrency=args.concurrency,
            p95_target_ms=args.p95_target_ms, max_error_rate=args.max_error_rate,
            min_requests=args.min_requests, window_seconds=args.window_seconds,
        )
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError) as error:
        print(json.dumps({"format": FORMAT, "status": "INVALID", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
