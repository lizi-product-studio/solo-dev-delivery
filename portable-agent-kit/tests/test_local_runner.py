import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SYSTEM = KIT.parent
sys.path.insert(0, str(KIT))
from core.local_checks import file_bytes, run_check


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.session = self.root / "control"
        self.cli = KIT / "scripts/session.py"

    def invoke(self, op, *args, ok=True):
        proc = subprocess.run([sys.executable, str(self.cli), op, "--session", str(self.session), *map(str, args)], capture_output=True, text=True)
        if ok:
            self.assertEqual(0, proc.returncode, proc.stderr)
            return json.loads(proc.stdout)
        self.assertNotEqual(0, proc.returncode)
        return proc

    def init(self, name="software.json", contract=None):
        value = contract or json.loads((KIT / "examples/contracts" / name).read_text())
        path = self.root / "contract.json"
        path.write_text(json.dumps(value))
        return self.invoke("init", "--workspace", self.workspace, "--contract", path)

    def write_config(self, **changes):
        retry = {"attempts": 2, "unknown_result": "reconcile", **changes}
        (self.workspace / "config.json").write_text(json.dumps({"retry": retry}))

    def test_wrong_model_output_cannot_finish_and_recovers_after_repair(self):
        self.init()
        self.write_config(attempts=200)
        self.assertEqual("FAIL", self.invoke("check", "--check", "AC1")["verdict"])
        self.assertNotEqual("COMPLETED", self.invoke("finish")["status"])
        self.write_config()
        self.invoke("check", "--check", "AC1")
        self.assertNotEqual("COMPLETED", self.invoke("finish")["status"])
        self.invoke("check", "--check", "AC2")
        self.assertEqual("COMPLETED", self.invoke("finish")["status"])

    def test_readback_revalidates_all_checks_after_an_artifact_changes(self):
        self.init()
        self.write_config()
        for check in ("AC1", "AC2"):
            self.invoke("check", "--check", check)
        self.write_config(unknown_result="blind_retry")
        self.assertNotEqual("COMPLETED", self.invoke("finish")["status"])

    def test_process_restart_returns_next_missing_acceptance(self):
        self.init()
        self.write_config()
        self.invoke("check", "--check", "AC1")
        self.assertEqual("AC2", self.invoke("next")["check"]["id"])
        self.invoke("init", "--workspace", self.workspace, "--contract", self.root / "contract.json", ok=False)
        self.assertEqual("AC2", self.invoke("next")["check"]["id"])

    def test_repeated_failure_suggests_escalation_without_model_calls(self):
        self.init()
        self.write_config(attempts=100)
        self.invoke("check", "--check", "AC1")
        self.invoke("check", "--check", "AC1")
        result = self.invoke("next")
        self.assertEqual("ESCALATE_OR_REPLAN", result["next_action"])
        self.assertEqual(2, result["failed_observations"])

    def test_analysis_matches_frozen_input_and_does_not_allow_source_rewrite(self):
        source = self.workspace / "orders.csv"
        source.write_text("status,amount\npaid,10.20\npaid,0.10\nrefunded,1.05\n")
        self.init("analysis.json")
        (self.workspace / "summary.json").write_text('{"paid":"10.30","refunds":"1.05"}')
        for check in ("AC1", "AC2"):
            self.assertEqual("PASS", self.invoke("check", "--check", check)["verdict"])
        source.write_text("status,amount\npaid,10.30\n")
        self.assertEqual("RESTORE_OR_REAUTHORIZE_INPUTS", self.invoke("next")["next_action"])
        self.assertNotEqual("COMPLETED", self.invoke("finish")["status"])

    def test_marketing_mechanical_pass_is_not_domain_review(self):
        (self.workspace / "costs.csv").write_text("channel,cost\nA,500\nB,300\n")
        self.init("marketing.json")
        (self.workspace / "plan.json").write_text('{"budget":800,"execution_status":"draft_only"}')
        for check in ("AC1", "AC2"):
            self.assertEqual("PASS", self.invoke("check", "--check", check)["verdict"])
        self.assertEqual("UNKNOWN", self.invoke("check", "--check", "AC3")["verdict"])
        self.assertNotEqual("COMPLETED", self.invoke("finish")["status"])

    def test_no_probe_does_not_pass_and_arbitrary_evidence_is_not_a_cli_operation(self):
        c = json.loads((KIT / "examples/contracts/software.json").read_text())
        c["acceptance"][0].pop("probe")
        self.init(contract=c)
        self.assertEqual("UNKNOWN", self.invoke("check", "--check", "AC1")["verdict"])
        self.invoke("evidence", ok=False)

    def test_session_lock_is_not_stolen(self):
        self.init()
        (self.session / ".lock").mkdir()
        self.invoke("next", ok=False)
        self.assertTrue((self.session / ".lock").exists())

    def test_session_inside_artifact_workspace_is_rejected(self):
        self.session = self.workspace / "control"
        with self.assertRaises(AssertionError):
            self.init()
        self.assertFalse((self.session / "session.json").exists())

    def test_cannot_claim_shell_or_browser_capability(self):
        c = json.loads((KIT / "examples/contracts/software.json").read_text())
        c["policy"]["ux_required"] = True
        path = self.root / "contract.json"
        path.write_text(json.dumps(c))
        self.invoke("init", "--workspace", self.workspace, "--contract", path, ok=False)
        self.assertFalse((self.session / "session.json").exists())

    def test_path_traversal_and_symlink_outside_workspace_are_rejected(self):
        secret = self.root / "outside.txt"
        secret.write_text("private")
        (self.workspace / "link").symlink_to(secret)
        for path in ("../outside.txt", "link", str(secret)):
            with self.assertRaises(ValueError):
                file_bytes(self.workspace, path)

    def test_invalid_numbers_cannot_produce_valid_analysis(self):
        for amount in ("NaN", "Infinity", "", "not-a-number"):
            (self.workspace / "orders.csv").write_text("status,amount\npaid," + amount + "\n")
            (self.workspace / "summary.json").write_text('{"paid":0}')
            check = json.loads((KIT / "examples/contracts/analysis.json").read_text())["acceptance"][0]
            self.assertEqual("FAIL", run_check(self.workspace, check, ["summary.json"])["verdict"])

    def test_csv_filter_typo_fails_instead_of_claiming_zero(self):
        (self.workspace / "orders.csv").write_text("status,amount\npaid,10\n")
        (self.workspace / "summary.json").write_text('{"paid":0}')
        check = json.loads((KIT / "examples/contracts/analysis.json").read_text())["acceptance"][0]
        check["probe"]["where"] = {"stauts":"paid"}
        self.assertEqual("FAIL", run_check(self.workspace, check, ["summary.json"])["verdict"])
        check["probe"]["where"] = {"status":"refunded"}
        result = run_check(self.workspace, check, ["summary.json"])
        self.assertEqual("PASS", result["verdict"])
        self.assertIn("matched rows=0", result["observed"])

    def test_numeric_precision_does_not_hide_wrong_amounts(self):
        check = json.loads((KIT / "examples/contracts/analysis.json").read_text())["acceptance"][0]
        for csv_values, output in (("10000000000000000000000000000\npaid,1", "10000000000000000000000000000"),
                                   ("0.1", "0.100000000000000005"), ("1", "1e1000000"), ("0", "1e-1000000")):
            (self.workspace / "orders.csv").write_text("status,amount\npaid," + csv_values + "\n")
            (self.workspace / "summary.json").write_text('{"paid":' + output + '}')
            self.assertEqual("FAIL", run_check(self.workspace, check, ["summary.json"])["verdict"])
        (self.workspace / "orders.csv").write_text("status,amount\npaid,10000000000000000000000000000\npaid,1\n")
        (self.workspace / "summary.json").write_text('{"paid":"10000000000000000000000000001"}')
        self.assertEqual("PASS", run_check(self.workspace, check, ["summary.json"])["verdict"])

    def test_json_numbers_do_not_round_into_the_expected_value(self):
        check = {"probe":{"type":"json.value","path":"value.json","pointer":["n"],"expected":0.1}}
        (self.workspace / "value.json").write_text('{"n":0.100000000000000005}')
        self.assertEqual("FAIL", run_check(self.workspace, check, ["value.json"])["verdict"])
        (self.workspace / "value.json").write_text('{"n":0.1}')
        self.assertEqual("PASS", run_check(self.workspace, check, ["value.json"])["verdict"])

    def test_contract_decimal_cannot_silently_change_acceptance(self):
        c = json.loads((KIT / "examples/contracts/software.json").read_text())
        c["acceptance"][0]["probe"]["expected"] = "raw-decimal-to-replace"
        path = self.root / "contract.json"
        path.write_text(json.dumps(c).replace('"raw-decimal-to-replace"', '0.100000000000000005'))
        error = self.invoke("init", "--workspace", self.workspace, "--contract", path, ok=False)
        self.assertIn("cannot be preserved", error.stderr)
        self.assertFalse((self.session / "session.json").exists())

    def test_long_csv_field_is_a_recorded_failure_not_a_crash(self):
        (self.workspace / "orders.csv").write_text("status,amount,note\npaid,1," + "x" * 150000 + "\n")
        self.init("analysis.json")
        (self.workspace / "summary.json").write_text('{"paid":1}')
        result = self.invoke("check", "--check", "AC1")
        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual(1, self.invoke("next")["failed_observations"])

    def test_completed_session_does_not_accept_later_mutation(self):
        self.init()
        self.write_config()
        for check in ("AC1", "AC2"):
            self.invoke("check", "--check", check)
        self.assertEqual("COMPLETED", self.invoke("finish")["status"])
        self.invoke("check", "--check", "AC1", ok=False)
        self.write_config(attempts=99)
        self.assertEqual("STALE_COMPLETION", self.invoke("next")["status"])

    def test_session_contract_drift_is_not_silently_accepted(self):
        self.init()
        path = self.session / "session.json"
        record = json.loads(path.read_text())
        record["contract"]["acceptance"][0]["probe"]["expected"] = 99
        path.write_text(json.dumps(record))
        self.invoke("next", ok=False)


class ExportColdStartTests(unittest.TestCase):
    def test_export_runs_after_unzip_under_unrelated_path_without_personal_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = subprocess.run([sys.executable, str(SYSTEM / "scripts/export_skill.py"), str(root / "build/agi-like")], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr)
            result = json.loads(proc.stdout)
            with zipfile.ZipFile(result["archive"]) as bundle:
                self.assertTrue(all("/Users/" not in name and ".." not in Path(name).parts for name in bundle.namelist()))
                bundle.extractall(root / "new-machine")
            skill = root / "new-machine/agi-like"
            self.assertFalse((skill / "knowledge/preferences.json").exists())
            for p in skill.rglob("*"):
                if p.is_file():
                    self.assertNotIn(b"/Users/lizi", p.read_bytes(), str(p))
            workspace = root / "workspace"
            workspace.mkdir()
            cli = skill / "scripts/challenge.py"
            self.assertFalse((skill / "scripts/session.py").exists())
            self.assertTrue((skill / "scripts/core/state_machine.py").exists())
            self.assertTrue((skill / "scripts/control_plane.py").exists())
            self.assertTrue((skill / "control_plane/runtime.py").exists())
            self.assertFalse((skill / "assets/contracts").exists())
            control = root / "control"
            control.mkdir()
            def invoke(*args):
                r = subprocess.run([sys.executable, '-B', str(cli), *map(str, args)], cwd=root, capture_output=True, text=True)
                self.assertEqual(0, r.returncode, r.stderr)
                return json.loads(r.stdout)
            (workspace / 'TASK.txt').write_text('Difference preserves sign.')
            (workspace / 'business.py').write_text('def difference(a,b): return a-b\n')
            (control / 'review.py').write_text('import unittest\nfrom business import difference\nclass Check(unittest.TestCase):\n def test_sign(self): self.assertEqual(-2,difference(3,5))\n')
            self.assertEqual('FROZEN', invoke('freeze','--workspace',workspace,'--input','TASK.txt','--output','business.py','--packet',control/'packet.json')['status'])
            self.assertEqual('CHALLENGE_PASS', invoke('run','--packet',control/'packet.json','--test',control/'review.py','--receipt',control/'receipt.json')['status'])
            self.assertEqual('CHALLENGE_PASS', invoke('status','--receipt',control/'receipt.json')['status'])


if __name__ == "__main__":
    unittest.main()
