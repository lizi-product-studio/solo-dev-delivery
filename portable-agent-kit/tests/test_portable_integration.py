"""Real exported-package/installer/preflight checks, with no Agent/model calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
ROOT = KIT.parent
sys.path.insert(0, str(KIT))
from core.capabilities import normalize, preflight
from core.state_machine import probe, start


class CapabilityPreflightTests(unittest.TestCase):
    def test_existing_core_and_preflight_share_all_three_states(self):
        caps = {"fs.read": True, "shell.exec": "unknown", "browser.control": False,
                "model:deepseek": "available", "test.run": {"supported": True}}
        self.assertEqual(normalize(caps), probe(caps)["capabilities"])
        self.assertEqual("unknown", normalize(caps)["model:deepseek"])
        self.assertEqual("READY", preflight(caps, ["fs.read"])["status"])
        self.assertEqual("BLOCKED", preflight(caps, ["browser.control"])["status"])
        self.assertEqual("NEEDS_CONTEXT", preflight(caps, ["shell.exec"])["status"])
        self.assertEqual("NEEDS_CONTEXT", preflight(caps, ["missing"])["status"])

    def test_no_implicit_session_checkpoint_or_vacuous_readiness(self):
        self.assertEqual(["fs.read"], preflight({"fs.read": True}, ["fs.read"])["required"])
        self.assertEqual("NEEDS_CONTEXT", preflight({"fs.read": True})["status"])
        contract = {"id": "fixture", "goal": "fixture", "owner": "test", "authorization_ref": "test",
                    "candidate": "test", "scope": ["f"], "excluded": ["external"],
                    "acceptance": [{"id": "A", "expected": "f", "environment": "local"}],
                    "policy": {"workspace_write": False}, "required_capabilities": ["fs.read"]}
        caps = {"fs.read": True}
        self.assertEqual("READY", preflight(caps, contract=contract)["status"])
        state = start(contract, caps)
        self.assertEqual("BLOCKED", state["status"])
        self.assertIn("checkpoint.persist", state["required_capabilities"])
        self.assertIn("evidence.hash", state["required_capabilities"])

    def test_requirements_validate_and_merge_declared_flags_only(self):
        result = preflight({}, ["test.run"], {"required_capabilities": ["semantic.review"],
            "policy": {"workspace_write": True, "ux_required": True,
                       "required_capabilities": ["network"], "external_actions": True}})
        self.assertEqual(set(result["required"]), {"test.run", "semantic.review", "fs.read", "fs.write",
                         "shell.exec", "browser.open", "browser.control", "network", "external.authorized"})
        self.assertFalse(result["tools_executed"])
        self.assertFalse(result["authorization_granted"])
        for contract in ({"policy": {"ux_required": "true"}}, {"required_capabilities": "test.run"},
                         {"required_capabilities": [""]}, {"policy": []}):
            self.assertEqual("NEEDS_CONTEXT", preflight({}, contract=contract)["status"])


class ExportedAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="solo-host-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "export/solo-dev-delivery"
        self.env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                        USERPROFILE=str(self.base / "home"), HOME=str(self.base / "home"),
                        CODEX_HOME=str(self.base / "home/.codex"))
        self.env.pop("CODEX_THREAD_ID", None)
        self.env.pop("KIMI_CODE_HOME", None)
        self.call(ROOT / "scripts/export_skill.py", self.source)

    def call(self, script, *args, code=0, env=None):
        result = subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                                cwd=self.base, env=env or self.env, capture_output=True,
                                text=True, encoding="utf-8")
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def installer(self, *args, **kwargs):
        return self.call(self.source / "scripts/install_agent.py", *args, **kwargs)

    def test_archive_order_and_generated_protocol_are_platform_independent(self):
        archive = next(self.source.parent.glob("solo-dev-delivery-*.zip"))
        with zipfile.ZipFile(archive) as packed:
            self.assertEqual(sorted(packed.namelist()), packed.namelist())
            self.assertTrue(all(member.create_system == 3 for member in packed.infolist()))
            self.assertTrue(all(member.date_time == (2026, 10, 22, 0, 0, 0) for member in packed.infolist()))
            self.assertTrue(all(member.external_attr == 0o100644 << 16 for member in packed.infolist()))
        bundle = json.loads((self.source / "BUNDLE.json").read_text(encoding="utf-8"))
        paths = [row["path"] for row in bundle["files"]]
        self.assertEqual(sorted(paths), paths)
        for generated in ("BUNDLE.json", "scripts/core/__init__.py"):
            self.assertNotIn(b"\r\n", (self.source / generated).read_bytes())
        self.assertEqual((ROOT / "skill/solo-dev-delivery/SKILL.md").read_bytes(),
                         (self.source / "SKILL.md").read_bytes())

    def test_all_hosts_install_self_contained_payload_without_configuration(self):
        for host in ("generic", "zcode", "kimi"):
            with self.subTest(host=host):
                destination = self.base / host / "用户 Skills"
                args = ("--host", host, "--destination", destination)
                self.installer(*args, "--dry-run")
                self.assertFalse(destination.exists())
                self.installer(*args)
                self.installer(*args, "--verify")
                again = json.loads(self.installer(*args).stdout)
                self.assertFalse(again["changed"])
                self.assertEqual("unverified", again["host_loading"])
                installed = destination / "solo-dev-delivery"
                bundle = json.loads((installed / "BUNDLE.json").read_text(encoding="utf-8"))
                for row in bundle["files"]:
                    self.assertEqual(row["sha256"], hashlib.sha256((installed / row["path"]).read_bytes()).hexdigest())
                self.assertFalse((installed / "knowledge").exists())
                self.assertFalse((installed / "scripts/core/state_machine.py").exists())
                self.assertFalse((installed / "scripts/session.py").exists())
                self.assertTrue((installed / "licenses/superpowers-lite-MIT.txt").is_file())
                for row in bundle["files"]:
                    if row["path"].endswith(".md"):
                        text = (installed / row["path"]).read_text(encoding="utf-8")
                        self.assertNotIn("/Users/lizi", text)
                # All optional tools must start from this unrelated host location.
                self.call(installed / "scripts/challenge.py", "--help")
                manifest = self.base / "caps.json"
                manifest.write_text('{"capabilities":{"fs.read":true}}', encoding="utf-8")
                result = self.call(installed / "scripts/capability_probe.py", "--capabilities", manifest,
                                   "--require", "fs.read")
                self.assertEqual("READY", json.loads(result.stdout)["status"])

    def test_default_paths_and_explicit_generic_destination(self):
        for host, destination in (("zcode", self.base / "home/.zcode/skills"),
                                  ("kimi", self.base / "home/.kimi-code/skills")):
            self.installer("--host", host)
            self.assertTrue((destination / "solo-dev-delivery/BUNDLE.json").exists())
        env = dict(self.env, KIMI_CODE_HOME=str(self.base / "custom-kimi"))
        self.installer("--host", "kimi", env=env)
        self.assertTrue((self.base / "custom-kimi/skills/solo-dev-delivery").is_dir())
        self.installer(code=2)

    def test_conflict_verify_and_tampered_source_never_overwrite(self):
        destination = self.base / "skills"
        args = ("--destination", destination)
        self.installer(*args)
        target = destination / "solo-dev-delivery/SKILL.md"
        target.write_bytes(b"user edits")
        self.installer(*args, code=2)
        self.installer(*args, "--verify", code=2)
        self.assertEqual(b"user edits", target.read_bytes())
        (self.source / "SKILL.md").write_bytes(b"tampered")
        other = self.base / "must-not-exist"
        self.installer("--destination", other, code=2)
        self.assertFalse(other.exists())

    def test_missing_verify_unknown_files_and_overlap_are_rejected(self):
        destination = self.base / "missing"
        self.installer("--destination", destination, "--verify", code=2)
        self.assertFalse(destination.exists())
        self.installer("--destination", self.source.parent, code=2)
        (self.source / "secret.txt").write_text("not exported", encoding="utf-8")
        self.installer("--destination", destination, code=2)
        self.assertFalse(destination.exists())

    def test_generated_python_caches_do_not_break_installed_verification(self):
        destination = self.base / "skills"
        self.installer("--destination", destination)
        cache = destination / "solo-dev-delivery/scripts/__pycache__"
        cache.mkdir()
        (cache / "example.pyc").write_bytes(b"generated cache")
        self.installer("--destination", destination, "--verify")

    def test_unicode_paths_report_success_under_legacy_stdio(self):
        # A successful file operation must not become a failed CLI response just
        # because a host's pipe encoding cannot represent a path character.
        env = dict(self.env, PYTHONUTF8="0", PYTHONIOENCODING="cp936")
        source = self.base / "export-\U0001f9ea/solo-dev-delivery"
        exported = self.call(ROOT / "scripts/export_skill.py", source, env=env)
        self.assertEqual(str(source), json.loads(exported.stdout)["skill"])
        destination = self.base / "host-\U0001f680/skills"
        result = self.call(source / "scripts/install_agent.py", "--destination", destination, env=env)
        self.assertEqual("INSTALLED", json.loads(result.stdout)["status"])
        self.call(source / "scripts/install_agent.py", "--destination", destination, "--verify", env=env)

    def test_interrupted_install_can_retry_without_a_partial_skill(self):
        spec = importlib.util.spec_from_file_location("solo_installer", ROOT / "scripts/install_agent.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        destination = self.base / "interrupted/skills"
        target = destination / "solo-dev-delivery"
        original_open = Path.open
        writes = []

        def fail_during_copy(path, mode="r", *args, **kwargs):
            if mode == "xb":
                writes.append(str(path))
                if len(writes) == 2:
                    raise OSError("fixture: write interrupted")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", fail_during_copy):
            with self.assertRaisesRegex(OSError, "write interrupted"):
                installer.install(self.source, destination)
        self.assertEqual(2, len(writes))
        self.assertFalse(target.exists(), "an incomplete Skill must never become discoverable")
        self.assertEqual([], list(destination.iterdir()))
        result = installer.install(self.source, destination)
        self.assertTrue(result["changed"])
        self.assertEqual("FILES_VERIFIED", installer.install(self.source, destination, verify=True)["status"])

    def test_moved_bundle_runs_real_gate_and_challenge_without_the_checkout(self):
        moved = self.base / "moved package/solo-dev-delivery"
        moved.parent.mkdir()
        self.source.rename(moved)
        self.assertFalse(self.source.exists())
        destination = self.base / "unrelated host/skills"
        self.call(moved / "scripts/install_agent.py", "--destination", destination)
        installed = destination / "solo-dev-delivery/scripts"
        workspace = self.base / "project"
        workspace.mkdir()
        (workspace / "request.md").write_text(
            "Return positive values unchanged.\nReject negative values.\n", encoding="utf-8")
        control = self.base / "gate-control"
        task = control / "task.json"

        def gate_call(*args, code=0):
            return json.loads(self.call(installed / "gate.py", *args, code=code).stdout)

        started = gate_call("start", "--workspace", workspace, "--input", "request.md",
                            "--output", "candidate.py", "--task", task)
        self.assertEqual("NEEDS_SOURCE_CHECKS", started["status"])
        test_file = control / "checks.py"
        test_file.write_text(
            "import unittest\nimport candidate\nclass Business(unittest.TestCase):\n"
            " def test_save(self): self.assertEqual(7, candidate.save(7))\n"
            " def test_negative(self):\n  with self.assertRaises(ValueError): candidate.save(-1)\n",
            encoding="utf-8")
        units = [row["id"] for row in started["source_units"]]
        self.assertEqual(2, len(units))
        plan = control / "plan.json"
        plan.write_text(json.dumps({"test_file": str(test_file), "checks": [
            {"id": "save", "test": "Business.test_save", "sources": [units[0]]},
            {"id": "negative", "test": "Business.test_negative", "sources": [units[1]]}]}), encoding="utf-8")
        self.assertEqual("CHECKS_SEALED", gate_call("seal", "--task", task, "--plan", plan)["status"])
        (workspace / "candidate.py").write_text(
            'def save(value):\n if value < 0: raise ValueError("negative")\n return value\n', encoding="utf-8")
        result = gate_call("check", "--task", task, "--run")
        self.assertEqual("DELIVERABLE_WITHIN_DECLARED_SCOPE", result["status"])
        self.assertEqual({"save", "negative"}, set(result["executed_checks"]))
        self.assertEqual([], gate_call("check", "--task", task, "--run")["executed_checks"])
        args = ("verify", "--workspace", workspace, "--input", "request.md", "--output", "candidate.py",
                "--test", test_file, "--report", self.base / "challenge-control/report.json")
        result = json.loads(self.call(installed / "challenge.py", *args).stdout)
        self.assertEqual("CHALLENGE_PASS", result["status"])
        self.assertFalse(result["reused"])
        self.assertTrue(json.loads(self.call(installed / "challenge.py", *args).stdout)["reused"])
        (workspace / "candidate.py").write_text('def save(value): return value\n', encoding="utf-8")
        self.assertNotEqual("DELIVERABLE_WITHIN_DECLARED_SCOPE", gate_call("check", "--task", task, code=1)["status"])
        result = self.call(installed / "challenge.py", "status", "--receipt", result["receipt"], code=1)
        self.assertEqual("STALE", json.loads(result.stdout)["status"])

    def test_target_link_cannot_redirect_installation(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.base / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        self.installer("--destination", link, code=2)
        self.assertEqual([], list(outside.iterdir()))

    def test_unknown_and_unsupported_exit_nonzero_without_state_files(self):
        manifest = self.base / "host.json"
        for state, expected in (("unknown", "NEEDS_CONTEXT"), ("unsupported", "BLOCKED")):
            manifest.write_text(json.dumps({"capabilities": {"test.run": state}}), encoding="utf-8")
            result = self.call(self.source / "scripts/capability_probe.py", "--capabilities", manifest,
                               "--require", "test.run", code=2)
            self.assertEqual(expected, json.loads(result.stdout)["status"])
        self.assertFalse((self.base / "session.json").exists())
        self.assertFalse((self.base / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
