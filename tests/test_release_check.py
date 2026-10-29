import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_check


class ReleaseCheckTests(unittest.TestCase):
    def make_release(self, root: Path):
        skill = root / "skill" / "agi-like"
        (skill / "agents").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            '---\nname: agi-like\nmetadata:\n  version: "1.0.0"\n---\n',
            encoding="utf-8",
        )
        (skill / "agents" / "openai.yaml").write_text("name: test\n", encoding="utf-8")
        archive = root / "agi-like-1.0.0.zip"
        files = {}
        for path in sorted(skill.rglob("*")):
            if path.is_file():
                rel = path.relative_to(skill).as_posix()
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        bundle = {"version": "1.0.0", "files": [{"path": p, "sha256": h} for p, h in files.items()]}
        with zipfile.ZipFile(archive, "w") as zf:
            for rel in files:
                zf.writestr(f"agi-like/{rel}", (skill / rel).read_bytes())
            zf.writestr("agi-like/BUNDLE.json", json.dumps(bundle))
        checksum = root / "agi-like-1.0.0.zip.sha256"
        checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  agi-like-1.0.0.zip\n", encoding="utf-8")
        return skill, archive, checksum

    def test_valid_release_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            skill, archive, checksum = self.make_release(Path(temp))
            result = release_check.check_package(skill, archive, checksum)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(2, result["bundle_files"])

    def test_archive_hash_drift_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            skill, archive, checksum = self.make_release(Path(temp))
            archive.write_bytes(archive.read_bytes() + b"drift")
            with self.assertRaises(release_check.ReleaseError):
                release_check.check_package(skill, archive, checksum)

    def test_installed_directory_must_match_archive_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill, archive, checksum = self.make_release(root)
            installed = root / "installed"
            for path in skill.rglob("*"):
                if path.is_file():
                    target = installed / path.relative_to(skill)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
            # The archive contains the generated bundle while the source test
            # directory does not, so add the same bundle bytes to the installed
            # tree before comparing.
            with zipfile.ZipFile(archive) as zf:
                (installed / "BUNDLE.json").write_bytes(zf.read("agi-like/BUNDLE.json"))
            result = release_check.check_installed(installed, archive)
            self.assertEqual("PASS", result["status"])
            (installed / "SKILL.md").write_text("changed", encoding="utf-8")
            with self.assertRaises(release_check.ReleaseError):
                release_check.check_installed(installed, archive)


if __name__ == "__main__":
    unittest.main()
