#!/usr/bin/env python3
"""Check that a versioned Skill release is self-contained and reproducible.

This is a packaging check. It does not run a model, contact a host, or turn
the autonomy pilot into a quality claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional


class ReleaseError(ValueError):
    pass


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_metadata(skill: Path) -> tuple[str, str]:
    source = skill / "SKILL.md"
    if not source.is_file():
        raise ReleaseError(f"missing Skill metadata: {source}")
    text = source.read_text(encoding="utf-8")
    name_match = re.search(r'^name: ([A-Za-z0-9][A-Za-z0-9-]*)$', text, re.MULTILINE)
    version_match = re.search(r'^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$', text, re.MULTILINE)
    if not name_match or not version_match or not SEMVER.fullmatch(version_match.group(1)):
        raise ReleaseError("Skill metadata must contain a valid name and semantic version")
    return name_match.group(1), version_match.group(1)


def load_bundle(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        bundle = json.loads(zf.read(name + "/BUNDLE.json").decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid BUNDLE.json: {exc}") from exc
    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), list):
        raise ReleaseError("BUNDLE.json files must be a list")
    return bundle


def check_package(skill: Path, archive: Path, checksum: Path | None) -> dict:
    name, version = read_metadata(skill)
    if archive.name != f"{name}-{version}.zip":
        raise ReleaseError(f"archive name does not match metadata: {archive.name}")
    if not archive.is_file():
        raise ReleaseError(f"missing archive: {archive}")
    if checksum is not None:
        if not checksum.is_file():
            raise ReleaseError(f"missing checksum file: {checksum}")
        expected = checksum.read_text(encoding="utf-8").split()[0]
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ReleaseError("checksum file must start with a SHA-256 digest")
        actual = digest(archive)
        if actual != expected:
            raise ReleaseError("archive checksum does not match the checksum file")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        prefix = f"{name}/"
        if any(not item.startswith(prefix) for item in names):
            raise ReleaseError("archive contains a path outside its Skill root")
        bundle = load_bundle(zf, name)
        if bundle.get("name", name) != name:
            raise ReleaseError("BUNDLE.json name does not match Skill metadata")
        if bundle.get("version") != version:
            raise ReleaseError("BUNDLE.json version does not match Skill metadata")
        listed = {item.get("path"): item.get("sha256") for item in bundle["files"]
                  if isinstance(item, dict)}
        if not listed or len(listed) != len(bundle["files"]):
            raise ReleaseError("BUNDLE.json contains invalid or duplicate file entries")
        expected_names = {prefix + path for path in listed}
        actual_names = {item for item in names if item != prefix + "BUNDLE.json"}
        if actual_names != expected_names:
            raise ReleaseError("archive file set does not match BUNDLE.json")
        for path, expected_hash in listed.items():
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
                raise ReleaseError(f"invalid bundle path: {path}")
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise ReleaseError(f"invalid bundle hash: {path}")
            if digest_bytes(zf.read(prefix + path)) != expected_hash:
                raise ReleaseError(f"bundle hash mismatch: {path}")
    return {"status": "PASS", "version": version, "archive": str(archive),
            "archive_sha256": digest(archive), "bundle_files": len(listed)}


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def directory_snapshot(root: Path) -> dict[str, bytes]:
    """Read a Skill directory without following links or accepting caches."""
    root = root.resolve()
    if not root.is_dir():
        raise ReleaseError("installed Skill directory is missing: " + str(root))
    result = {}
    for path in sorted(root.rglob("*")):
        if path.name == "__pycache__" or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ReleaseError("installed Skill contains a symlink: " + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def check_installed(installed: Path, archive: Path) -> dict:
    """Compare an installed directory with the versioned archive payload."""
    installed_payload = directory_snapshot(installed)
    with zipfile.ZipFile(archive) as zf:
        root_names = sorted({name.split("/", 1)[0] for name in zf.namelist() if "/" in name})
        if len(root_names) != 1:
            raise ReleaseError("archive must contain one Skill root")
        prefix = root_names[0] + "/"
        archive_payload = {
            name[len(prefix):]: zf.read(name)
            for name in zf.namelist()
            if name.startswith(prefix) and name != prefix + "BUNDLE.json"
        }
        bundle_bytes = zf.read(prefix + "BUNDLE.json")
        archive_payload["BUNDLE.json"] = bundle_bytes
    if set(installed_payload) != set(archive_payload):
        missing = sorted(set(archive_payload) - set(installed_payload))
        extra = sorted(set(installed_payload) - set(archive_payload))
        raise ReleaseError("installed file set differs; missing=%s extra=%s" % (missing, extra))
    mismatches = [name for name in sorted(archive_payload)
                  if installed_payload[name] != archive_payload[name]]
    if mismatches:
        raise ReleaseError("installed bytes differ: " + ", ".join(mismatches))
    return {"status": "PASS", "installed": str(installed.resolve()),
            "files": len(installed_payload), "archive": str(archive.resolve())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path, help="canonical Skill directory")
    parser.add_argument("archive", type=Path, help="versioned ZIP archive")
    parser.add_argument("--checksum", type=Path, help="optional .sha256 file")
    parser.add_argument("--installed", type=Path,
                        help="optional installed Skill directory to compare byte-for-byte")
    args = parser.parse_args(argv)
    try:
        result = check_package(args.skill, args.archive, args.checksum)
        if args.installed:
            result["installed_check"] = check_installed(args.installed, args.archive)
    except (OSError, ReleaseError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
