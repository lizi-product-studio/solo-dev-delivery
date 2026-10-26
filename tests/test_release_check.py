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
        skill = root / "solo-dev-delivery"
        (skill / "agents").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            '---\nname: solo-dev-delivery\nmetadata:\n  version: "1.0.0"\n---\n',
            encoding="utf-8",
        )
        (skill / "agents" / "openai.yaml").write_text("name: test\n", encoding="utf-8")
        archive = root / "solo-dev-delivery-1.0.0.zip"
        files = {}
        for path in sorted(skill.rglob("*")):
            if path.is_file():
                rel = path.relative_to(skill).as_posix()
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        bundle = {"version": "1.0.0", "files": [{"path": p, "sha256": h} for p, h in files.items()]}
        bundle_bytes = (json.dumps(bundle, separators=(",", ":")) + "\n").encode()
        (skill / "BUNDLE.json").write_bytes(bundle_bytes)
        with zipfile.ZipFile(archive, "w") as zf:
            for rel in files:
                zf.writestr(f"solo-dev-delivery/{rel}", (skill / rel).read_bytes())
            zf.writestr("solo-dev-delivery/BUNDLE.json", bundle_bytes)
        checksum = root / "solo-dev-delivery-1.0.0.zip.sha256"
        checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  solo-dev-delivery-1.0.0.zip\n", encoding="utf-8")
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

    def test_source_hash_drift_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            skill, archive, checksum = self.make_release(Path(temp))
            (skill / "agents/openai.yaml").write_text("changed\n", encoding="utf-8")
            with self.assertRaises(release_check.ReleaseError):
                release_check.check_package(skill, archive, checksum)

    def test_bundle_rejects_traversal_paths(self):
        with self.assertRaises(release_check.ReleaseError):
            release_check.validate_bundle_path("../outside.txt")
        with self.assertRaises(release_check.ReleaseError):
            release_check.validate_bundle_path(r"scripts\..\outside.txt")


if __name__ == "__main__":
    unittest.main()
