"""Codex CLI process adapter; transport completion is not business acceptance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


class CodexHostError(RuntimeError):
    pass


class CodexCliAdapter:
    def __init__(self, executable=None):
        self.executable = str(executable or os.environ.get("CODEX_CLI_PATH", "codex"))

    def run(self, prompt, *, workspace, session, model=None, timeout=900, log_dir=None, sandbox='workspace-write'):
        if sandbox not in {'read-only', 'workspace-write'}:
            raise ValueError('unsupported sandbox')
        command = [sys.executable, self.executable] if self.executable.endswith('.py') else [self.executable]
        command += ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
                    "--sandbox", sandbox, "--cd", str(workspace)]
        if model:
            command += ["-m", model]
        command += ["-"]
        events = []
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace") as process:
            # communicate drains both pipes concurrently. Waiting on stdout
            # first can deadlock when stderr fills and defeats the timeout.
            try:
                stdout, stderr = process.communicate(prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                self._save(log_dir, stdout, stderr, process.returncode, timed_out=True)
                raise CodexHostError("Codex timeout; result unknown, inspect persisted logs before retry")
            self._save(log_dir, stdout, stderr, process.returncode)
            malformed = False
            for line in stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("event must be an object")
                except (json.JSONDecodeError, ValueError):
                    malformed = True
                    continue
                events.append(event)
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") in {
                        "command_execution", "mcp_tool_call", "web_search"}:
                    if item.get("status") == "failed" or item.get("exit_code") not in (None, 0):
                        status = "FAILED"
                    elif item.get("status") == "completed":
                        status = "SUCCEEDED"
                    else:
                        status = "UNKNOWN"
                    session.tool_result(item.get("id", "codex-item"), status,
                                        source="codex-cli", event=event)
            if process.returncode != 0:
                errors = [e for e in events if e.get("type") in {"error", "turn.failed"}]
                raise CodexHostError("Codex exit %s: %s %s" %
                                     (process.returncode, json.dumps(errors, ensure_ascii=False), stderr[-1000:]))
        if malformed or any(e.get("type") in {"error", "turn.failed"} for e in events):
            raise CodexHostError("Codex stream contains invalid or failed events")
        if not any(e.get("type") == "turn.completed" for e in events):
            raise CodexHostError("Codex stream ended without turn.completed")
        return events

    @staticmethod
    def _save(directory, stdout, stderr, code, timed_out=False):
        if directory is None:
            return
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / "stdout.jsonl").write_text(stdout, encoding="utf-8")
        (root / "stderr.log").write_text(stderr, encoding="utf-8")
        (root / "process.json").write_text(json.dumps({
            "exit_code": code, "timed_out": timed_out}), encoding="utf-8")
