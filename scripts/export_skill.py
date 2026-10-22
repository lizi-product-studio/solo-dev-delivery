#!/usr/bin/env python3
"""Build a self-contained Agent Skill. No personal knowledge or local runtime."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    destination = Path(destination).resolve()
    if destination.name != "solo-dev-delivery":
        raise ValueError("destination folder must be named solo-dev-delivery")
    if destination.exists():
        raise ValueError("destination exists; preserve it and choose a new parent")
    archive = destination.parent / "solo-dev-delivery-0.2.0.zip"
    if archive.exists():
        raise ValueError("archive exists; choose a new parent")
    source = ROOT / "skill/solo-dev-delivery"
    mappings = [(source / "SKILL.md", "SKILL.md"),
                (source / "agents/openai.yaml", "agents/openai.yaml")]
    mappings += [(p, "references/" + p.name) for p in (source / "references").glob("*.md")]
    kit = ROOT / "portable-agent-kit"
    mappings += [(kit / "scripts/session.py", "scripts/session.py")]
    mappings += [(p, "scripts/core/" + p.name) for p in (kit / "core").glob("*.py")]
    mappings += [(p, "assets/contracts/" + p.name) for p in (kit / "examples/contracts").glob("*.json")]
    destination.mkdir(parents=True)
    for src, rel in mappings:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    files = [{"path": p.relative_to(destination).as_posix(),
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(destination.rglob("*")) if p.is_file()]
    (destination / "BUNDLE.json").write_text(json.dumps({
        "version": "0.2.0", "format": "Agent Skills", "python": "3.9+", "files": files,
        "personal_data_included": False, "scheduler": "none",
        "platform_status": "format-and-local-runtime-verified; platform loading must be checked by its host",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for p in sorted(destination.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(p.relative_to(destination.parent).as_posix(), (2026, 10, 22, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, p.read_bytes())
    return {"skill": str(destination), "archive": str(archive), "files": len(files) + 1,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    try:
        print(json.dumps(export(parser.parse_args().destination), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
