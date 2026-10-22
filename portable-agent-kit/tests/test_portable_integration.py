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
                        USERPROFILE=str(self.base / "home"), HOME=str(self.base / "home"))
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
