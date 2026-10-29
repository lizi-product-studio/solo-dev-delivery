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
NAME = "agi-like"


def export(destination):
    destination = Path(destination).resolve()
    if destination.name != NAME:
        raise ValueError("destination folder must be named " + NAME)
    if destination.exists():
        raise ValueError("destination exists; preserve it and choose a new parent")
    source = ROOT / "skill" / NAME
    match = re.search(r'^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$', (source / 'SKILL.md').read_text(encoding='utf-8'), re.M)
    if not match:
        raise ValueError('Skill metadata must contain a semantic version')
    version = match.group(1)
    archive = destination.parent / (NAME + "-" + version + ".zip")
    if archive.exists():
        raise ValueError("archive exists; choose a new parent")
    mappings = [(source / "SKILL.md", "SKILL.md"),
                (source / "agents/openai.yaml", "agents/openai.yaml")]
    mappings += [(p, "references/" + p.name) for p in (source / "references").glob("*.md")]
    kit = ROOT / "portable-agent-kit"
    # A standalone Skill needs the lightweight verification tool, not the full
    # persistent protocol or old contract examples. Native compatibility stays
    # in the complete system and is tested there.
    mappings += [(kit / "scripts" / name, "scripts/" + name) for name in (
        "challenge.py", "gate.py", "eval_contract.py", "host_replay.py", "blind_review.py",
        "agent_queue.py", "agent_run.py", "bridge.py", "control_plane.py", "independent_verify.py", "runtime_event.py", "self_improvement.py",
        "capacity_policy.py", "checkpoint_receipt.py", "db_inspect.py",
        "load_report.py", "quality_contract.py")]
    mappings += [(kit / "scripts/capability_probe.py", "scripts/capability_probe.py"),
                 (kit / "scripts/capability_receipt.py", "scripts/capability_receipt.py"),
                 (kit / "CONTROL_PLANE.md", "CONTROL_PLANE.md"),
                 (kit / "AGENT_RUNTIME.md", "AGENT_RUNTIME.md"),
                 (kit / "MANIFEST.json", "MANIFEST.json"),
                 (ROOT / "scripts/release_check.py", "scripts/release_check.py"),
                 (ROOT / "scripts/install_agent.py", "scripts/install_agent.py"),
                 (ROOT / "THIRD_PARTY.md", "THIRD_PARTY.md"),
                 (ROOT / "vendor/superpowers-lite/LICENSE", "licenses/superpowers-lite-MIT.txt")]
    mappings += [(kit / "core" / name, "scripts/core/" + name)
                 for name in ("local_checks.py", "json_relations.py", "coverage.py", "sensitivity.py", "capabilities.py", "decisions.py", "state_machine.py")]
    mappings += [(p, "control_plane/" + p.relative_to(kit / "control_plane").as_posix())
                 for p in (kit / "control_plane").rglob("*.py")]
    mappings += [(p, "agent_runtime/" + p.relative_to(kit / "agent_runtime").as_posix())
                 for p in (kit / "agent_runtime").rglob("*.py")]
    destination.mkdir(parents=True)
    for src, rel in mappings:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Git checks out text with different line endings on Windows/Linux.
        # Release identity uses canonical UTF-8/LF, not checkout conversion.
        payload=src.read_bytes()
        if src.suffix.lower() in {'.md','.py','.json','.yaml','.yml','.txt'}:
            payload=payload.replace(b'\r\n',b'\n')
        target.write_bytes(payload)
    # The native core initializer imports the persistent protocol. The thin
    # export intentionally exposes only local verification modules.
    (destination / 'scripts/core/__init__.py').write_bytes(b'"""Bounded local verification helpers."""\n')
    files = [{"path": p.relative_to(destination).as_posix(),
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(destination.rglob("*"), key=lambda item: item.relative_to(destination).as_posix()) if p.is_file()]
    (destination / "BUNDLE.json").write_bytes((json.dumps({
        "name": NAME, "version": version, "format": "Agent Skills", "python": "3.9+", "files": files,
        "source_text_normalization": "UTF-8 text with LF line endings",
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
