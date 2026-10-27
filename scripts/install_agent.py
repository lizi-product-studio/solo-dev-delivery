#!/usr/bin/env python3
"""Install a self-contained solo-dev-delivery bundle into an Agent Skill root.

File preflight and link checks adapted from Superpowers Lite e753ab1 (MIT).
See THIRD_PARTY.md. Does not edit host settings or execute host tools.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

NAME = "solo-dev-delivery"


def absolute(path):
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def reject_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Symlink or reparse point is not allowed: " + str(item))


def snapshot(root):
    reject_links(root)
    if not root.is_dir():
        raise ValueError("Missing Skill directory: " + str(root))
    result = {}

    def visit(directory):
        for item in sorted(directory.iterdir()):
            # Python cache files do not belong to the exported payload.
            if item.name == "__pycache__" or item.suffix == ".pyc":
                continue
            reject_links(item)
            mode = item.lstat().st_mode
            relative = item.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                visit(item)
            elif stat.S_ISREG(mode):
                result[relative] = item.read_bytes()
            else:
                raise ValueError("Special file is not allowed: " + str(item))

    visit(root)
    return result


def bundle_payload(source):
    payload = snapshot(source)
    bundle = json.loads(payload.get("BUNDLE.json", b"null").decode("utf-8-sig"))
    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), list):
        raise ValueError("Source must be an exported Skill with BUNDLE.json; run scripts/export_skill.py first")
    if bundle.get("personal_data_included") is not False or bundle.get("scheduler") != "none":
        raise ValueError("Expected a standalone export without personal data or scheduler")
    listed = set()
    for row in bundle["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid bundle file record")
        path = row["path"]
        if path in listed or path not in payload or path == "BUNDLE.json":
            raise ValueError("Duplicate or missing bundle file: " + path)
        listed.add(path)
        if hashlib.sha256(payload[path]).hexdigest() != row.get("sha256"):
            raise ValueError("Bundle file hash mismatch: " + path)
    if set(payload) != listed | {"BUNDLE.json"}:
        raise ValueError("Bundle has unlisted files; preserve a clean export")
    text = payload.get("SKILL.md", b"").decode("utf-8-sig").replace("\r\n", "\n")
    if not text.startswith("---\n") or "\nname: " + NAME + "\n" not in text.split("---", 2)[1] + "\n":
        raise ValueError("Bundle Skill name must be " + NAME)
    return payload


def destination_for(host):
    if host == "zcode":
        return Path.home() / ".zcode/skills"
    if host == "kimi":
        return Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code") / "skills"
    raise ValueError("Generic hosts require --destination pointing to their documented Skill search root")


def install(source, destination, *, dry_run=False, verify=False):
    source, destination = absolute(source), absolute(destination)
    reject_links(destination)
    target = destination / NAME
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and installation target must not overlap")
    payload = bundle_payload(source)
    reject_links(target)
    exists = target.exists()
    if exists and snapshot(target) != payload:
        raise ValueError("Changed installed Skill; back it up outside the Skill root before replacing it")
    if verify and not exists:
        raise ValueError("Installed Skill is missing: " + str(target))
    if not (verify or dry_run or exists):
        destination.mkdir(parents=True, exist_ok=True)
        # Keep an incomplete copy outside the host's discoverable Skill path.
        # The staging directory shares the destination filesystem so publication
        # is one rename; a write failure cleans up only this invocation's files.
        with tempfile.TemporaryDirectory(prefix=".solo-install-", dir=str(destination)) as temporary:
            staged = Path(temporary) / NAME
            staged.mkdir()
            for relative, data in payload.items():
                path = staged / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(data)
            if snapshot(staged) != payload:
                raise ValueError("Staged Skill differs from the verified source")
            reject_links(target)
            if target.exists():
                raise ValueError("Installation target appeared during copy; preserve it and retry verification")
            staged.rename(target)
    return {"status": "FILES_VERIFIED" if verify else ("DRY_RUN" if dry_run else "INSTALLED"),
            "destination": str(target), "files": len(payload), "changed": not (verify or dry_run or exists),
            "host_loading": "unverified", "automatic_routing": "unverified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("generic", "zcode", "kimi"), default="generic")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1], help="Exported Skill root")
    parser.add_argument("--destination", type=Path, help="Host Skill search root, not the final solo-dev-delivery directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        destination = args.destination if args.destination is not None else destination_for(args.host)
        print(json.dumps(install(args.source, destination, dry_run=args.dry_run, verify=args.verify), ensure_ascii=True, indent=2))
    except (OSError, ValueError, TypeError, IndexError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
