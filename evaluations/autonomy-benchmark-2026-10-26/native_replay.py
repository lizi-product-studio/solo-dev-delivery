#!/usr/bin/env python3
"""Run a small, real Codex replay for the frozen autonomy pilot.

This is an operator-facing harness, not a simulator. It invokes the local
Codex CLI in an isolated profile, stores raw logs outside source control and
derives the receipt from the independent fixture checker. It intentionally
supports only the software quick-patch fixture until the remaining 11 task
adapters have their own independent checkers.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from datetime import datetime, timezone

PILOT = Path(__file__).resolve().parent
REPO = PILOT.parents[1]
FIXTURE_ROOT = REPO / "evaluations" / "skill-comparison-2026-10-23"
sys.path.insert(0, str(FIXTURE_ROOT / "design"))
import fixture_tools as fixtures  # type: ignore  # noqa: E402

REAL_HOME = Path(os.environ.get("USERPROFILE", Path.home()))
REAL_CODEX = REAL_HOME / ".codex"
CODEX = shutil.which("codex")
MODEL_CONFIG = REAL_CODEX / "config.toml"
TASK_ID = "software-quick-patch"
FIXTURE_ID = fixtures.TASK_IDS[0]
CONTRACT = PILOT / "pilot-contract.json"
PACKAGE = REPO / "packages" / "solo-dev-delivery-0.13.0.zip"
SOLO_012_COMMIT = "c57659e7d1722edd08fbfcd15426ea7c79c32087"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest(p)
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def base_environment(profile: Path, codex_home: Path) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "LANG", "LC_ALL", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "ALLUSERSPROFILE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(CODEX_HOME=str(codex_home), USERPROFILE=str(profile), HOME=str(profile),
               APPDATA=str(profile / "AppData" / "Roaming"),
               LOCALAPPDATA=str(profile / "AppData" / "Local"),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env


def setup(root: Path, arm: str, label: str) -> dict:
    if CODEX is None:
        raise RuntimeError("codex executable is unavailable")
    if not MODEL_CONFIG.is_file() or not (REAL_CODEX / "auth.json").is_file():
        raise RuntimeError("local Codex model/auth files are unavailable")
    base = root / label
    profile, workspace = base / "profile", base / "workspace"
    codex_home = profile / ".codex"
    skills = workspace / ".agents" / "skills"
    base.mkdir(parents=True)
    codex_home.mkdir(parents=True)
    fixtures.build(FIXTURE_ID, workspace)
    skills.mkdir(parents=True)
    candidate_sha = None
    revision = None
    if arm == "solo-0.13.0":
        shutil.copytree(REPO / "skill" / "solo-dev-delivery", skills / "solo-dev-delivery")
        candidate_sha = digest(PACKAGE)
        revision = "git:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    elif arm == "solo-0.12.0":
        archive = subprocess.check_output(["git", "archive", "--format=tar", SOLO_012_COMMIT, "skill/solo-dev-delivery"], cwd=REPO)
        staging = base / "_skill-012"
        staging.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(staging)
        shutil.copytree(staging / "skill" / "solo-dev-delivery", skills / "solo-dev-delivery")
        candidate_sha = hashlib.sha256(("solo-0.12.0:" + SOLO_012_COMMIT).encode("ascii")).hexdigest()
        revision = "git:" + SOLO_012_COMMIT
    elif arm == "baseline":
        candidate_sha = hashlib.sha256(b"baseline").hexdigest()
        revision = "baseline:none"
    else:
        raise ValueError("unsupported arm: " + arm)
    # Only the minimum local auth/cache files needed to start Codex are copied
    # into the disposable profile. They are never written to the evidence set.
    shutil.copyfile(REAL_CODEX / "auth.json", codex_home / "auth.json")
    if (REAL_CODEX / "models_cache.json").is_file():
        shutil.copyfile(REAL_CODEX / "models_cache.json", codex_home / "models_cache.json")
    model = tomllib.loads(MODEL_CONFIG.read_text(encoding="utf-8")).get("model", "gpt-6.1-sol")
    config = [
        'model = ' + json.dumps(model),
        'model_reasoning_effort = "high"', 'service_tier = "priority"',
        'approval_policy = "never"', 'sandbox_mode = "danger-full-access"',
        'forced_login_method = "chatgpt"', 'web_search = "disabled"',
        'cli_auth_credentials_store = "file"', '[features]', 'memories = false',
        'plugins = false', 'multi_agent = true', '[memories]', 'use_memories = false',
        'generate_memories = false', '[agents]', 'max_threads = 2', 'max_depth = 1',
    ]
    (codex_home / "config.toml").write_text("\n".join(config) + "\n", encoding="utf-8")
    env = base_environment(profile, codex_home)
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, env=env)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Pilot"], check=True, env=env)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "pilot@localhost.invalid"], check=True, env=env)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True, env=env, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "--quiet", "-m", "Frozen public task"], check=True, env=env, capture_output=True)
    return {"base": base, "profile": profile, "home": codex_home, "workspace": workspace, "env": env,
            "candidate_sha256": candidate_sha, "revision": revision}


def select_submission(ctx: dict, final_text: Path) -> Path:
    root = ctx["workspace"]
    roots = []
    listing = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                             check=False, capture_output=True, text=True, encoding="utf-8", env=ctx["env"])
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[9:]).resolve()
            if path != root.resolve() and ctx["base"].resolve() in path.parents:
                roots.append(path)
    if not roots:
        return root
    text = final_text.read_text(encoding="utf-8", errors="replace") if final_text.is_file() else ""
    mentioned = [p for p in roots if str(p).replace("\\", "/").casefold() in text.replace("\\", "/").casefold()]
    return mentioned[0] if len(mentioned) == 1 else roots[0] if len(roots) == 1 else root


def run(args) -> dict:
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    label = f"{args.task}__{args.arm}__r{args.repeat}"
    ctx = setup(root, args.arm, label)
    final = root / label / "final.txt"
    events = root / label / "events.jsonl"
    stderr = root / label / "stderr.log"
    prompt = (
        ("使用 $solo-dev-delivery。\n" if args.arm == "solo-0.13.0" else "")
        + "在当前项目中完成 TASK.md 的任务。先读取公开任务与契约；只在当前项目内工作，禁止联网、真实账号和全局配置。"
          "完成后实际读取交付物并简要说明结果。"
    )
    started_at = utc()
    started = time.monotonic()
    with events.open("wb") as out, stderr.open("wb") as err:
        proc = subprocess.Popen([CODEX, "exec", "--json", "--ignore-rules", "-C", str(ctx["workspace"]), "-o", str(final), "-"],
                                cwd=ctx["workspace"], env=ctx["env"], stdin=subprocess.PIPE, stdout=out, stderr=err)
        timed_out = False
        try:
            proc.communicate(prompt.encode("utf-8"), timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], check=False, capture_output=True)
            proc.wait(timeout=15)
    elapsed = round(time.monotonic() - started, 4)
    ended_at = utc()
    selected = select_submission(ctx, final)
    evaluation = fixtures.evaluate(FIXTURE_ID, selected, control_workspace=ctx["workspace"])
    trial_root = root / label
    evidence_root = trial_root / "evidence"
    evidence_root.mkdir()
    evaluation_path = evidence_root / "independent-evaluation.json"
    save(evaluation_path, evaluation)
    final_path = evidence_root / "agent-final.txt"
    final_path.write_text(final.read_text(encoding="utf-8", errors="replace") if final.is_file() else "", encoding="utf-8")
    candidate_sha = ctx["candidate_sha256"]
    revision = ctx["revision"]
    if candidate_sha is None or revision is None:
        raise RuntimeError("candidate identity was not bound during setup")
    contract_sha = digest(CONTRACT)
    def ref(path: Path, evidence_id: str) -> dict:
        return {"id": evidence_id, "path": path.relative_to(evidence_root).as_posix(), "sha256": digest(path),
                "trial_id": label, "contract_id": json.loads(CONTRACT.read_text(encoding="utf-8"))["contract_id"],
                "source_revision": revision, "candidate_sha256": candidate_sha}
    refs = [ref(evaluation_path, "independent-evaluation"), ref(final_path, "agent-final")]
    passed = bool(evaluation.get("completed"))
    receipt = {
        "schema_version": "0.13", "contract_id": json.loads(CONTRACT.read_text(encoding="utf-8"))["contract_id"],
        "contract_sha256": contract_sha, "trial_id": label, "task_id": args.task, "arm_id": args.arm,
        "repeat": args.repeat, "status": "completed" if passed else ("unknown" if timed_out else "failed"),
        "source_revision": revision, "candidate_sha256": candidate_sha,
        "evidence_status": "DONE" if passed else "NEEDS_EVIDENCE", "route": "BOUNDED" if args.arm == "solo-0.13.0" else None,
        "boundary": "AUTO" if passed else "STOP", "first_pass_success": None, "final_success": passed,
        "user_correction_count": None, "recoverable_blocker": None, "handoff_required": None,
        "handoff_justified": None, "recovery_completed": None, "independent_progress": passed,
        "stale_side_effect": False, "unknown_reported_as_success": False if not timed_out else None,
        "execution_seconds": elapsed, "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
        "evidence_refs": refs, "observations": {
            "changed": {"status": "observed" if passed else "unknown", "evidence_refs": refs[:1]},
            "readback": {"status": "observed" if passed else "unknown", "evidence_refs": refs[:1]},
        },
        "provenance": {"mode": "native_agent", "host": "codex-cli", "skill_revision": revision,
                       "started_at": started_at, "ended_at": ended_at, "run_id": label,
                       "task_objective": "完成一个明确、可回退的小修改并读回结果",
                       "evidence_sha256": {r["path"]: r["sha256"] for r in refs}},
    }
    save(trial_root / "receipt.json", receipt)
    save(trial_root / "evaluation.json", evaluation)
    return {"receipt": receipt, "evaluation": evaluation, "workspace": str(selected), "elapsed": elapsed,
            "timed_out": timed_out, "events": str(events), "stderr": str(stderr)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=TASK_ID, choices=[TASK_ID])
    parser.add_argument("--arm", required=True, choices=["baseline", "solo-0.12.0", "solo-0.13.0"])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        print(json.dumps({"status": "HARNESS_ERROR", "error": repr(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["receipt"]["status"], "trial_id": result["receipt"]["trial_id"],
                      "final_success": result["receipt"]["final_success"], "seconds": result["elapsed"],
                      "workspace": result["workspace"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
