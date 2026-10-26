#!/usr/bin/env python3
"""Build a self-contained Agent Skill. No personal knowledge or local runtime."""
import argparse
import hashlib
import json
import shutil
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    destination = Path(destination).resolve()
    if destination.name != "solo-dev-delivery":
        raise ValueError("destination folder must be named solo-dev-delivery")
    if destination.exists():
        raise ValueError("destination exists; preserve it and choose a new parent")
    source = ROOT / "skill/solo-dev-delivery"
    match = re.search(r'^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$', (source / 'SKILL.md').read_text(encoding='utf-8'), re.M)
    if not match:
        raise ValueError('Skill metadata must contain a semantic version')
    version = match.group(1)
    archive = destination.parent / ("solo-dev-delivery-" + version + ".zip")
    if archive.exists():
        raise ValueError("archive exists; choose a new parent")
    mappings = [(source / "SKILL.md", "SKILL.md"),
                (source / "agents/openai.yaml", "agents/openai.yaml")]
    mappings += [(p, "references/" + p.name) for p in (source / "references").glob("*.md")]
    kit = ROOT / "portable-agent-kit"
    # A standalone Skill needs the lightweight verification tool, not the full
    # persistent protocol or old contract examples. Native compatibility stays
    # in the complete system and is tested there.
    mappings += [(kit / "scripts" / name, "scripts/" + name) for name in ("challenge.py", "gate.py", "eval_contract.py", "host_replay.py", "blind_review.py")]
    mappings += [(kit / "scripts/capability_probe.py", "scripts/capability_probe.py"),
                 (ROOT / "scripts/install_agent.py", "scripts/install_agent.py"),
                 (ROOT / "THIRD_PARTY.md", "THIRD_PARTY.md"),
                 (ROOT / "vendor/superpowers-lite/LICENSE", "licenses/superpowers-lite-MIT.txt")]
    mappings += [(kit / "core" / name, "scripts/core/" + name)
                 for name in ("local_checks.py", "json_relations.py", "coverage.py", "sensitivity.py", "capabilities.py", "decisions.py")]
    destination.mkdir(parents=True)
    for src, rel in mappings:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    # The native core initializer imports the persistent protocol. The thin
    # export intentionally exposes only local verification modules.
    (destination / 'scripts/core/__init__.py').write_bytes(b'"""Bounded local verification helpers."""\n')
    files = [{"path": p.relative_to(destination).as_posix(),
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(destination.rglob("*"), key=lambda item: item.relative_to(destination).as_posix()) if p.is_file()]
    (destination / "BUNDLE.json").write_bytes((json.dumps({
        "version": version, "format": "Agent Skills", "python": "3.9+", "files": files,
        "personal_data_included": False, "scheduler": "none",
        "platform_status": "format-and-local-runtime-verified; platform loading must be checked by its host",
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted(destination.rglob("*"), key=lambda item: item.relative_to(destination).as_posix()):
            if p.is_file():
                info = zipfile.ZipInfo(p.relative_to(destination.parent).as_posix(), (2026, 10, 22, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, p.read_bytes())
    return {"skill": str(destination), "archive": str(archive), "files": len(files) + 1,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        print(json.dumps(export(parser.parse_args().destination), ensure_ascii=True, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
