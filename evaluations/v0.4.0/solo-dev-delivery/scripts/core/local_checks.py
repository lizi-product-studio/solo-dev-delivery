"""Read-only, bounded artifact checks. No model/network/shell execution.

These checks prove specified mechanical properties, not product judgement,
domain expertise, a user's authorization or a platform's real capabilities.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from .json_relations import pointer, relation_pointer, render_table, table_block

MAX_BYTES = 5 * 1024 * 1024


def load_json(text):
    def invalid(value):
        raise ValueError("non-finite JSON number: " + value)
    def contract_float(value):
        result = float(value)
        if not Decimal(str(result)).is_finite() or Decimal(str(result)) != Decimal(value):
            raise ValueError("JSON decimal cannot be preserved; use a decimal string or a precise numeric check")
        return result
    return json.loads(text, parse_constant=invalid, parse_float=contract_float)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def file_bytes(workspace, relative):
    root = Path(workspace).resolve(strict=True)
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("artifact path must be relative to workspace")
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("artifact escapes workspace") from None
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("artifact must be a file of at most 5 MiB")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("artifact grew beyond 5 MiB")
    return data


def artifact(workspace, path):
    data = file_bytes(workspace, path)
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a business number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("number must be finite")
    digits = result.as_tuple()
    if len(digits.digits) > 100 or abs(digits.exponent) > 100:
        raise ValueError("number exceeds supported 100-digit / exponent-100 range")
    return result


def precise_json(text):
    return json.loads(text, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("non-finite JSON number")))


def equal_json(actual, expected):
    if isinstance(actual, Decimal):
        return type(expected) in (int, float) and number(actual) == number(expected)
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equal_json(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equal_json(a, e) for a, e in zip(actual, expected))
    return actual == expected


def relation_json(text):
    """New relations reject ambiguous duplicate keys as well as inexact numbers."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key: " + key)
            value[key] = item
        return value
    return json.loads(text, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("non-finite JSON number")),
                      object_pairs_hook=unique)


def relation_number(value, numeric_strings=False):
    if isinstance(value, str):
        if not numeric_strings or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value):
            raise ValueError("numeric strings require numeric_strings=true and JSON decimal syntax")
    elif type(value) not in (int, Decimal):
        raise ValueError("relation value must be an exact JSON number")
    return Fraction(number(value))


def aggregate_json(target, source, probe):
    """Sum exact known values; a single null makes the complete total unknown."""
    allowed = {"type", "path", "pointer", "source", "rows", "value", "known_sum",
               "known_count", "unknown_count", "empty", "numeric_strings"}
    if set(probe) - allowed:
        raise ValueError("unknown json.aggregate parameters: " + ", ".join(sorted(set(probe) - allowed)))
    empty = probe.get("empty", "error")
    if empty not in ("error", "zero"):
        raise ValueError("empty must be error or zero")
    numeric_strings = probe.get("numeric_strings", False)
    if type(numeric_strings) is not bool:
        raise ValueError("numeric_strings must be boolean")
    rows = relation_pointer(source, probe["rows"])
    if not isinstance(rows, list):
        raise ValueError("aggregate rows pointer must select a JSON array")
    if not rows and empty == "error":
        raise ValueError("empty aggregate requires explicit empty=zero")
    # Validate even for an explicitly permitted empty array.
    if (not isinstance(probe["value"], list)
            or any(type(p) not in (str, int) or (type(p) is int and p < 0) for p in probe["value"])):
        raise ValueError("aggregate value must be a list-form pointer")
    known_sum, known_count, unknown_count = Fraction(0), 0, 0
    for row in rows:
        value = relation_pointer(row, probe["value"])
        if value is None:
            unknown_count += 1
        else:
            known_sum += relation_number(value, numeric_strings)
            known_count += 1
    actual = relation_pointer(target, probe["pointer"])
    passed = actual is None if unknown_count else relation_number(actual, numeric_strings) == known_sum
    expected = {"known_sum": known_sum, "known_count": Fraction(known_count),
                "unknown_count": Fraction(unknown_count)}
    for key, value in expected.items():
        if key in probe:
            matches = relation_number(relation_pointer(target, probe[key]), numeric_strings) == value
            passed = passed and matches
    observed = ("Complete total must be " + ("null" if unknown_count else str(known_sum))
                + "; known_sum=" + str(known_sum) + "; known_count=" + str(known_count)
                + "; unknown_count=" + str(unknown_count) + ".")
    return passed, observed


def run_check(workspace, check, scope):
    """Execute a contract-defined check; return actual verdict and all hashes."""
    result = {"verdict": "UNKNOWN", "observed": "", "artifacts": []}
    try:
        probe = check.get("probe", {})
        kind, path = probe.get("type"), probe.get("path")
        if kind not in ("text.equals", "text.contains", "json.equals", "json.value", "csv.reconcile", "json.aggregate", "json.table"):
            result["observed"] = "Host or independent reviewer required for this acceptance; local checker cannot verify it."
            return result
        if path not in scope:
            raise ValueError("check artifact is outside contract scope")
        if kind in ("json.aggregate", "json.table") and ".." in Path(path).parts:
            raise ValueError("relation artifact path must not contain parent traversal")
        data = file_bytes(workspace, path)
        result["artifacts"].append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
        text = data.decode("utf-8-sig")
        if kind == "text.equals":
            passed = text == probe["expected"]
            result["observed"] = "Compared complete UTF-8 text."
        elif kind == "text.contains":
            expected = probe["expected"]
            if not isinstance(expected, list) or not expected or not all(isinstance(x, str) and x for x in expected):
                raise ValueError("text.contains needs non-empty expected strings")
            missing = [x for x in expected if x not in text]
            passed = not missing
            result["observed"] = "Missing required fragments: " + canonical(missing) + "; semantic quality is not established."
        elif kind in ("json.equals", "json.value"):
            value = precise_json(text)
            if kind == "json.value":
                value = pointer(value, probe["pointer"])
            passed = equal_json(value, probe["expected"])
            result["observed"] = "Actual JSON value (exact numbers displayed as strings): " + json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:2000]
        elif kind in ("json.aggregate", "json.table"):
            source = probe.get("source", path)
            if source not in scope:
                raise ValueError("relation source is outside contract scope")
            if not isinstance(source, str) or ".." in Path(source).parts:
                raise ValueError("relation source path must be relative without parent traversal")
            if source == path:
                source_text = text
            else:
                raw = file_bytes(workspace, source)
                result["artifacts"].append({"path": source, "sha256": hashlib.sha256(raw).hexdigest()})
                source_text = raw.decode("utf-8-sig")
            source_json = relation_json(source_text)
            if kind == "json.aggregate":
                target = source_json if source == path else relation_json(text)
                passed, result["observed"] = aggregate_json(target, source_json, probe)
            else:
                allowed = {"type", "path", "source", "rows", "columns", "block"}
                if set(probe) - allowed:
                    raise ValueError("unknown json.table parameters: " + ", ".join(sorted(set(probe) - allowed)))
                expected = render_table(relation_pointer(source_json, probe["rows"]), probe["columns"])
                passed = table_block(text, probe["block"]) == expected
                result["observed"] = ("Compared generated table block " + probe["block"]
                                      + " with canonical JSON cells and row order; surrounding free-text semantics are not verified.")
        else:
            source = probe["source"]
            raw = file_bytes(workspace, source)
            result["artifacts"].append({"path": source, "sha256": hashlib.sha256(raw).hexdigest()})
            rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            if not rows.fieldnames or probe["column"] not in rows.fieldnames:
                raise ValueError("CSV column does not exist")
            if len(rows.fieldnames) != len(set(rows.fieldnames)):
                raise ValueError("duplicate CSV header")
            where = probe.get("where", {})
            if not isinstance(where, dict) or not set(where) <= set(rows.fieldnames):
                raise ValueError("CSV filter references a missing column")
            total = Fraction(0)
            matched_rows = 0
            for row in rows:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("malformed CSV row")
                if all(row[k] == str(v) for k, v in where.items()):
                    total += Fraction(number(row[probe["column"]]))
                    matched_rows += 1
            actual = Fraction(number(pointer(precise_json(text), probe["pointer"])))
            passed = actual == total
            result["observed"] = "Actual=" + str(actual) + "; source total=" + str(total) + "; matched rows=" + str(matched_rows)
        result["verdict"] = "PASS" if passed else "FAIL"
    except (ValueError, OSError, KeyError, TypeError, IndexError, InvalidOperation, csv.Error, OverflowError) as exc:
        result["verdict"], result["observed"] = "FAIL", type(exc).__name__ + ": " + str(exc)
    return result


def verifier(workspace, contract, input_snapshot):
    """Host callback. Recompute artifacts and the contract check each time."""
    checks = {c["id"]: c for c in contract["acceptance"]}

    def verify(evidence):
        try:
            for item in input_snapshot:
                if artifact(workspace, item["path"]) != item:
                    return False
            check = checks[evidence["check_id"]]
            observed = run_check(workspace, check, contract["scope"])
            return (evidence.get("kind") == "local-check"
                    and evidence.get("environment") == check["environment"]
                    and evidence.get("uri") == check.get("probe", {}).get("path")
                    and evidence.get("verdict") == observed["verdict"]
                    and evidence.get("observed") == observed["observed"]
                    and evidence.get("artifacts") == observed["artifacts"]
                    and bool(observed["artifacts"])
                    and evidence.get("sha256") == observed["artifacts"][0]["sha256"])
        except (ValueError, OSError, KeyError, TypeError):
            return False
    return verify
