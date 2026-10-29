"""Catch stale downloadable packages in the normal native regression suite."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_check import check_package, read_metadata


class RepositoryPackageTests(unittest.TestCase):
    def test_current_package_matches_a_fresh_source_export(self):
        with tempfile.TemporaryDirectory(prefix="solo-release-") as temporary:
            name, version = read_metadata(ROOT / "skill/agi-like")
            skill = Path(temporary) / name
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts/export_skill.py"), str(skill)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            rebuilt = Path(temporary) / (name + "-" + version + ".zip")
            committed = ROOT / "packages" / rebuilt.name
            checksum = committed.with_suffix(".zip.sha256")
            # Historical immutable releases remain for reproducibility.
            self.assertTrue(committed.is_file())
            self.assertTrue(checksum.is_file())
            self.assertEqual("PASS", check_package(skill, committed, checksum)["status"])
            self.assertEqual(hashlib.sha256(rebuilt.read_bytes()).hexdigest(),
                             hashlib.sha256(committed.read_bytes()).hexdigest(),
                             "Rebuild the downloadable ZIP after changing exported source")


if __name__ == "__main__":
    unittest.main()
