#!/usr/bin/env python3
"""Check that an exported Skill and its versioned ZIP are self-contained.

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
from pathlib import Path, PurePosixPath
from typing import Optional


class ReleaseError(ValueError):
    pass


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def read_metadata(skill: Path) -> tuple[str, str]:
    source = skill / "SKILL.md"
    if not source.is_file():
        raise ReleaseError(f"missing Skill metadata: {source}")
    match = re.search(r'^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$',
                      source.read_text(encoding="utf-8"), re.MULTILINE)
    if not match or not SEMVER.fullmatch(match.group(1)):
        raise ReleaseError("Skill metadata must contain a semantic version")
    return "solo-dev-delivery", match.group(1)


def validate_bundle_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseError(f"invalid bundle path: {value}")
    path = PurePosixPath(value)
    parts = value.split("/")
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in parts) or
            re.match(r"^[A-Za-z]:", value)):
        raise ReleaseError(f"invalid bundle path: {value}")
    return value


def read_bundle(path: Path) -> tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
        bundle = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid BUNDLE.json: {exc}") from exc
    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), list):
        raise ReleaseError("BUNDLE.json files must be a list")
    return bundle, raw


def listed_files(bundle: dict) -> dict[str, str]:
    listed = {}
    for item in bundle["files"]:
        if not isinstance(item, dict):
            raise ReleaseError("BUNDLE.json contains an invalid file entry")
        path = validate_bundle_path(item.get("path"))
        expected_hash = item.get("sha256")
        if path in listed:
            raise ReleaseError(f"duplicate BUNDLE.json path: {path}")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ReleaseError(f"invalid bundle hash: {path}")
        listed[path] = expected_hash
    if not listed:
        raise ReleaseError("BUNDLE.json must list at least one file")
    return listed


def check_package(skill: Path, archive: Path, checksum: Optional[Path] = None) -> dict:
    name, version = read_metadata(skill)
    if archive.name != f"{name}-{version}.zip":
        raise ReleaseError(f"archive name does not match metadata: {archive.name}")
    if not archive.is_file():
        raise ReleaseError(f"missing archive: {archive}")
    source_bundle, source_bundle_bytes = read_bundle(skill / "BUNDLE.json")
    if source_bundle.get("version") != version:
        raise ReleaseError("source BUNDLE.json version does not match Skill metadata")
    source_files = listed_files(source_bundle)
    actual_source_files = {
        path.relative_to(skill).as_posix()
        for path in skill.rglob("*") if path.is_file() and path.name != "BUNDLE.json"
    }
    if actual_source_files != set(source_files):
        raise ReleaseError("source file set does not match BUNDLE.json")
    for path, expected_hash in source_files.items():
        source_path = skill / path
        if source_path.is_symlink():
            raise ReleaseError(f"source bundle file must not be a symlink: {path}")
        try:
            source_path.resolve().relative_to(skill.resolve())
        except ValueError as exc:
            raise ReleaseError(f"source bundle path escapes export root: {path}") from exc
        if digest(source_path) != expected_hash:
            raise ReleaseError(f"source BUNDLE hash mismatch: {path}")
    if checksum is not None:
        if not checksum.is_file():
            raise ReleaseError(f"missing checksum file: {checksum}")
        fields = checksum.read_text(encoding="utf-8").split()
        if not fields or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise ReleaseError("checksum file must start with a SHA-256 digest")
        if digest(archive) != fields[0]:
            raise ReleaseError("archive checksum does not match the checksum file")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        if len(names) != len(set(names)):
            raise ReleaseError("archive contains duplicate paths")
        prefix = f"{name}/"
        if any(not item.startswith(prefix) or item.endswith("/") for item in names):
            raise ReleaseError("archive contains a path outside its Skill root or a directory entry")
        archive_bundle = zf.read(prefix + "BUNDLE.json")
        if archive_bundle != source_bundle_bytes:
            raise ReleaseError("archive BUNDLE.json does not match exported source")
        bundle, _ = read_bundle(skill / "BUNDLE.json")
        if bundle.get("version") != version:
            raise ReleaseError("BUNDLE.json version does not match Skill metadata")
        expected_names = {prefix + path for path in source_files}
        expected_names.add(prefix + "BUNDLE.json")
        if set(names) != expected_names:
            raise ReleaseError("archive file set does not match BUNDLE.json")
        for path, expected_hash in source_files.items():
            if digest_bytes(zf.read(prefix + path)) != expected_hash:
                raise ReleaseError(f"archive BUNDLE hash mismatch: {path}")
    return {"status": "PASS", "version": version, "archive": str(archive),
            "archive_sha256": digest(archive), "bundle_files": len(source_files)}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path, help="self-contained exported Skill directory")
    parser.add_argument("archive", type=Path, help="versioned ZIP archive")
    parser.add_argument("--checksum", type=Path, help="optional .sha256 file")
    args = parser.parse_args(argv)
    try:
        result = check_package(args.skill, args.archive, args.checksum)
    except (OSError, ReleaseError, zipfile.BadZipFile, KeyError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
