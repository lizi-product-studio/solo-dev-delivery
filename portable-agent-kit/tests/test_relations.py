"""Executable counterexamples for exact, scoped JSON artifact relations."""
import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from core.json_relations import block_markers, render_table
from core.local_checks import MAX_BYTES, relation_json, run_check, verifier


class RelationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.scope = ["rows.json", "summary.json", "plan.md"]

    def write(self, path, value):
        (self.workspace / path).write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def aggregate(self, **overrides):
        probe = {"type": "json.aggregate", "path": "summary.json", "pointer": ["total"],
                 "source": "rows.json", "rows": ["rows"], "value": ["amount"],
                 "known_sum": ["known_sum"], "known_count": ["known_count"],
                 "unknown_count": ["unknown_count"]}
        probe.update(overrides)
        return {"id": "REL", "environment": "local", "probe": probe}

    def result(self, check, scope=None):
        return run_check(self.workspace, check, self.scope if scope is None else scope)

    def summary(self, total, known_sum, known_count, unknown_count):
        self.write("summary.json", {"total": total, "known_sum": known_sum,
                                    "known_count": known_count, "unknown_count": unknown_count})

    def table(self):
        return {"id": "TABLE", "environment": "local", "probe": {
            "type": "json.table", "path": "plan.md", "source": "rows.json", "rows": ["rows"],
            "columns": [{"label": "Task", "pointer": ["task"]},
                        {"label": "Person", "pointer": ["person"]},
                        {"label": "Cost", "pointer": ["cost"]}], "block": "assignments"}}

    def write_table(self, check=None, before="# Plan\n", after="\nOther prose is freely editable.\n"):
        check = check or self.table()
        source = relation_json((self.workspace / "rows.json").read_text())
        body = render_table(source["rows"], check["probe"]["columns"])
        start, end = block_markers(check["probe"]["block"])
        self.write("plan.md", before + start + "\n" + body + "\n" + end + after)
        return body

    def evidence(self, check):
        observed = self.result(check)
        return {"kind": "local-check", "check_id": check["id"], "environment": "local",
                "uri": check["probe"]["path"], "sha256": observed["artifacts"][0]["sha256"],
                **observed}

    def test_unknown_full_total_is_not_the_known_subtotal(self):
        self.write("rows.json", {"rows": [{"amount": 10}, {"amount": None}]})
        self.summary(10, 10, 1, 1)
        self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
        self.summary(None, 10, 1, 1)
        self.assertEqual("PASS", self.result(self.aggregate())["verdict"])
        for field, bad in (("known_sum", 0), ("known_count", 2), ("unknown_count", 0)):
            summary = {"total": None, "known_sum": 10, "known_count": 1, "unknown_count": 1}
            summary[field] = bad
            self.write("summary.json", summary)
            self.assertEqual("FAIL", self.result(self.aggregate())["verdict"], field)

    def test_all_unknown_and_optional_subtotals_have_explicit_meanings(self):
        self.write("rows.json", {"rows": [{"amount": None}, {"amount": None}]})
        self.summary(None, 0, 0, 2)
        self.assertEqual("PASS", self.result(self.aggregate())["verdict"])
        check = self.aggregate()
        for key in ("known_sum", "known_count", "unknown_count"):
            check["probe"].pop(key)
        self.assertEqual("PASS", self.result(check)["verdict"])
        self.summary(0, 0, 0, 2)
        self.assertEqual("FAIL", self.result(check)["verdict"])

    def test_empty_requires_an_explicit_zero_policy_and_missing_never_means_zero(self):
        self.write("rows.json", {"rows": []})
        self.summary(0, 0, 0, 0)
        self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
        self.assertEqual("PASS", self.result(self.aggregate(empty="zero"))["verdict"])
        for value in ({"rows": [{}]}, {"rows": {}}, {}):
            self.write("rows.json", value)
            self.assertEqual("FAIL", self.result(self.aggregate(empty="zero"))["verdict"])
        self.write("rows.json", {"rows": []})
        self.assertEqual("FAIL", self.result(self.aggregate(empty="zero", value=[-1]))["verdict"])

    def test_numeric_strings_are_opt_in_and_use_decimal_syntax(self):
        self.write("rows.json", {"rows": [{"amount": "0.10"}, {"amount": "0.20"}]})
        self.summary("0.30", "0.3", 2, 0)
        self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
        self.assertEqual("PASS", self.result(self.aggregate(numeric_strings=True))["verdict"])
        for value in ("", "unknown", "NaN", "Infinity", "1_000", " 1", "01"):
            self.write("rows.json", {"rows": [{"amount": value}]})
            self.assertEqual("FAIL", self.result(self.aggregate(numeric_strings=True))["verdict"], value)

    def test_large_integer_and_small_decimal_sums_do_not_round(self):
        for source, total, wrong in (
                ('[{"amount":10000000000000000000000000000},{"amount":1}]',
                 '10000000000000000000000000001', '10000000000000000000000000000'),
                ('[{"amount":0.1},{"amount":0.2}]', '0.3', '0.300000000000000005'),
                ('[{"amount":1e-100},{"amount":1e-100}]', '2e-100', '1e-100')):
            self.write("rows.json", '{"rows":' + source + '}')
            self.write("summary.json", '{"total":' + total + ',"known_sum":' + total + ',"known_count":2,"unknown_count":0}')
            self.assertEqual("PASS", self.result(self.aggregate())["verdict"])
            # Correct subtotal must never rescue a wrong complete total.
            self.write("summary.json", '{"total":' + wrong + ',"known_sum":' + total + ',"known_count":2,"unknown_count":0}')
            self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])

    def test_booleans_nonfinite_and_out_of_range_numbers_fail(self):
        self.summary(1, 1, 1, 0)
        for value in ("true", "NaN", "Infinity", "1e101", "1e-101", "1" * 101):
            self.write("rows.json", '{"rows":[{"amount":' + value + '}]}')
            self.assertEqual("FAIL", self.result(self.aggregate())["verdict"], value)
        self.write("rows.json", {"rows": [{"amount": 1}]})
        for target in (True, None, "1"):
            self.summary(target, 1, 1, 0)
            self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
        self.summary(1, 1, True, 0)
        self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])

    def test_same_file_is_supported_and_hashes_one_snapshot(self):
        self.write("summary.json", {"rows": [{"amount": 3}], "total": 3,
                                    "known_sum": 3, "known_count": 1, "unknown_count": 0})
        result = self.result(self.aggregate(source="summary.json"))
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(["summary.json"], [a["path"] for a in result["artifacts"]])

    def test_source_change_invalidates_old_evidence_even_when_sum_is_unchanged(self):
        check = self.aggregate()
        self.write("rows.json", {"rows": [{"amount": 3}], "note": "first"})
        self.summary(3, 3, 1, 0)
        evidence = self.evidence(check)
        verify = verifier(self.workspace, {"acceptance": [check], "scope": self.scope}, [])
        self.assertTrue(verify(evidence))
        self.assertEqual(["summary.json", "rows.json"], [a["path"] for a in evidence["artifacts"]])
        self.write("rows.json", {"rows": [{"amount": 3}], "note": "changed"})
        self.assertEqual("PASS", self.result(check)["verdict"])
        self.assertFalse(verify(evidence))

    def test_source_scope_and_path_escape_fail_for_both_relation_types(self):
        self.write("rows.json", {"rows": [{"amount": 3, "task": "A", "person": "Lee", "cost": 3}]})
        self.summary(3, 3, 1, 0)
        self.write_table()
        outside = self.root / "outside.json"
        outside.write_text('{"rows":[{"amount":3}]}')
        (self.workspace / "link.json").symlink_to(outside)
        for base in (self.aggregate(), self.table()):
            self.assertEqual("FAIL", self.result(base, [base["probe"]["path"]])["verdict"])
            for path in ("../outside.json", str(outside), "link.json", "sub/../rows.json"):
                check = copy.deepcopy(base)
                check["probe"]["source"] = path
                self.assertEqual("FAIL", self.result(check, self.scope + [path])["verdict"], path)
            check = copy.deepcopy(base)
            check["probe"]["path"] = "../outside.json"
            self.assertEqual("FAIL", self.result(check, self.scope + ["../outside.json"])["verdict"])

    def test_source_and_target_files_are_bounded(self):
        self.write("rows.json", {"rows": [{"amount": 1}]})
        self.summary(1, 1, 1, 0)
        for filename in ("rows.json", "summary.json"):
            original = (self.workspace / filename).read_bytes()
            (self.workspace / filename).write_bytes(b" " * (MAX_BYTES + 1))
            self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
            (self.workspace / filename).write_bytes(original)

    def test_table_person_cell_and_row_order_must_match(self):
        self.write("rows.json", {"rows": [{"task": "J6", "person": "Chen", "cost": None},
                                           {"task": "J7", "person": "Wu", "cost": 5}]})
        body = self.write_table()
        self.assertIn("| J6 | Chen | 未知 |", body)
        self.assertEqual("PASS", self.result(self.table())["verdict"])
        original = (self.workspace / "plan.md").read_text()
        self.write("plan.md", original.replace("| J6 | Chen |", "| J6 | Wu |"))
        self.assertEqual("FAIL", self.result(self.table())["verdict"])
        self.write("plan.md", original.replace(body, "\n".join(body.splitlines()[:2] + list(reversed(body.splitlines()[2:])))))
        self.assertEqual("FAIL", self.result(self.table())["verdict"])
        self.write("plan.md", original.replace("未知", "0"))
        self.assertEqual("FAIL", self.result(self.table())["verdict"])

    def test_table_only_constrains_its_named_block_and_accepts_other_layouts(self):
        self.write("rows.json", {"rows": [{"task": "A", "person": "Lee", "cost": 1}]})
        check = self.table()
        self.write_table(before="A completely different title\n\n| Free | Table |\n| --- | --- |\n", after="\n<!-- generated:other:start -->\nanything\n<!-- generated:other:end -->\n")
        self.assertEqual("PASS", self.result(check)["verdict"])
        evidence = self.evidence(check)
        verify = verifier(self.workspace, {"acceptance": [check], "scope": self.scope}, [])
        self.assertTrue(verify(evidence))
        self.write("plan.md", "Extra prose\n" + (self.workspace / "plan.md").read_text())
        result = self.result(check)
        self.assertEqual("PASS", result["verdict"])
        self.assertIn("free-text semantics are not verified", result["observed"])
        self.assertFalse(verify(evidence))
        # Editors may normalize line endings; identical cell content still passes.
        self.write("plan.md", (self.workspace / "plan.md").read_text().replace("\n", "\r\n"))
        self.assertEqual("PASS", self.result(check)["verdict"])

    def test_table_escaping_is_deterministic_and_cannot_inject_rows_or_markers(self):
        source = {"rows": [{"task": "a|b\\c\r\nd", "person": "<b>*Lee*</b>\n<!-- generated:assignments:end -->", "cost": None}]}
        self.write("rows.json", source)
        body = self.write_table()
        self.assertEqual(3, len(body.splitlines()))
        self.assertIn("a&#124;b&#92;c<br>d", body)
        self.assertIn("&lt;b&gt;&#42;Lee&#42;&lt;/b&gt;<br>&lt;!--", body)
        self.assertEqual("PASS", self.result(self.table())["verdict"])

    def test_table_requires_unique_standalone_and_ordered_markers(self):
        self.write("rows.json", {"rows": [{"task": "A", "person": "Lee", "cost": 1}]})
        body = self.write_table()
        start, end = block_markers("assignments")
        for text in (body, start + "\n" + body, end + "\n" + body + "\n" + start,
                     start + "\n" + body + "\n" + end + "\n" + start + "\n" + end,
                     "prefix " + start + "\n" + body + "\n" + end):
            self.write("plan.md", text)
            self.assertEqual("FAIL", self.result(self.table())["verdict"])
        for bad in ("", "x\n<!--", "../x", 1):
            with self.assertRaises(ValueError):
                block_markers(bad)

    def test_table_exact_number_renderer_rejects_already_rounded_floats(self):
        columns = [{"label": "Amount", "pointer": ["n"]}]
        self.assertIn("10000000000000000000000000001", render_table([{"n": Decimal("10000000000000000000000000001")}], columns))
        for value in (0.1, float("inf"), Decimal("NaN"), Decimal("1e101"), {}, []):
            with self.assertRaises(ValueError):
                render_table([{"n": value}], columns)
        for pointer in ([-1], [True], "n"):
            with self.assertRaises(ValueError):
                render_table([], [{"label": "N", "pointer": pointer}])

    def test_duplicate_keys_bad_pointers_and_unknown_parameters_fail_closed(self):
        self.write("rows.json", '{"rows":[{"amount":null,"amount":1}]}')
        self.summary(1, 1, 1, 0)
        self.assertEqual("FAIL", self.result(self.aggregate())["verdict"])
        self.write("rows.json", {"rows": [{"amount": 1}]})
        for overrides in ({"value": [-1]}, {"rows": [True]}, {"known_smu": ["known_sum"]}):
            self.assertEqual("FAIL", self.result(self.aggregate(**overrides))["verdict"])
        self.write("rows.json", {"rows": [{"task": "A", "person": "Lee"}]})
        self.write("plan.md", "anything")
        self.assertEqual("FAIL", self.result(self.table())["verdict"])

    def test_all_legacy_probes_remain_compatible(self):
        self.write("plain.txt", "hello\n")
        self.write("old.json", '{"n":0.1,"total":"0.3"}')
        self.write("old.csv", "amount\n0.1\n0.2\n")
        cases = [
            {"type": "text.equals", "path": "plain.txt", "expected": "hello\n"},
            {"type": "text.contains", "path": "plain.txt", "expected": ["hello"]},
            {"type": "json.equals", "path": "old.json", "expected": {"n": 0.1, "total": "0.3"}},
            {"type": "json.value", "path": "old.json", "pointer": ["n"], "expected": 0.1},
            {"type": "csv.reconcile", "path": "old.json", "source": "old.csv", "column": "amount", "pointer": ["total"]},
        ]
        # Legacy csv sources need not be in output scope; preserve that established API.
        for probe in cases:
            self.assertEqual("PASS", self.result({"probe": probe}, [probe["path"]])["verdict"], probe["type"])
        self.assertEqual("UNKNOWN", self.result({"probe": {"type": "human.review"}})["verdict"])


if __name__ == "__main__":
    unittest.main()
