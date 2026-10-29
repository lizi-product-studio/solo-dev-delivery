"""Codex CLI process adapter; transport completion is not business acceptance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


class CodexHostError(RuntimeError):
    pass


class CodexHostActionUnknown(CodexHostError):
    """The decision subprocess may have acted outside Runtime's tool registry."""
    outcome_unknown = True

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


class CodexCliAdapter:
    def __init__(self, executable=None):
        self.executable = str(executable or os.environ.get("CODEX_CLI_PATH", "codex"))

    def run(self, prompt, *, workspace, session, model=None, timeout=900, log_dir=None, sandbox='workspace-write', reasoning_effort=None, output_schema=None, decision_only=False, instructions_file=None, ignore_user_config=False):
        if sandbox not in {'read-only', 'workspace-write'}:
            raise ValueError('unsupported sandbox')
        command = [sys.executable, self.executable] if self.executable.endswith('.py') else [self.executable]
        command += ["exec", "--json", "--ephemeral", "--skip-git-repo-check",
                    "--sandbox", sandbox, "--cd", str(workspace)]
        if model:
            command += ["-m", model]
        if output_schema is not None:
            command += ['--output-schema',str(output_schema)]
        if ignore_user_config:
            if not model:raise ValueError('isolated model configuration requires an explicit model')
            command += ['--ignore-user-config']
        if decision_only:
            command += ['--disable','shell_tool','-c','skills.include_instructions=false',
                        '-c','skills.bundled.enabled=false','-c','project_doc_max_bytes=0',
                        '-c','features.apps=false','-c','features.plugins=false',
                        '-c','features.remote_plugin=false']
        if instructions_file is not None:
            command += ['-c','model_instructions_file='+json.dumps(str(instructions_file))]
        if reasoning_effort is not None:
            if reasoning_effort not in {'low','medium','high','xhigh','max','ultra'}:
                raise ValueError('unsupported reasoning effort')
            command += ['-c','model_reasoning_effort='+json.dumps(reasoning_effort)]
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
                if decision_only:
                    raise CodexHostActionUnknown('decision subprocess timed out; host effects require readback',
                        details={'source':'codex-cli','log_dir':str(log_dir) if log_dir else None})
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
                if decision_only and event.get('type') in {'item.started','item.completed'}:
                    if not isinstance(item,dict) or item.get('type') not in {'reasoning','agent_message'}:
                        raise CodexHostActionUnknown('decision model emitted a forbidden host item; effect unknown',
                            details={'source':'codex-cli','log_dir':str(log_dir) if log_dir else None,
                                     'item':{key:item.get(key) for key in ('id','type','status')} if isinstance(item,dict) else {}})
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
            # An error notification is not a terminal turn event. A disconnected
            # stream must stay unknown even when the CLI also reports an error.
            if decision_only and (malformed or not any(e.get('type') in {'turn.completed','turn.failed'} for e in events)):
                raise CodexHostActionUnknown('decision subprocess ended with incomplete host events; effect unknown',
                    details={'source':'codex-cli','log_dir':str(log_dir) if log_dir else None,
                             'exit_code':process.returncode})
            if process.returncode != 0:
                errors = [e for e in events if e.get("type") in {"error", "turn.failed"}]
                raise CodexHostError("Codex exit %s: %s %s" %
                                     (process.returncode, json.dumps(errors, ensure_ascii=False), stderr[-1000:]))
        if malformed or any(e.get("type") in {"error", "turn.failed"} for e in events):
            if decision_only and malformed:
                raise CodexHostActionUnknown('invalid decision event stream; host effects require readback',
                    details={'source':'codex-cli','log_dir':str(log_dir) if log_dir else None})
            raise CodexHostError("Codex stream contains invalid or failed events")
        if not any(e.get("type") == "turn.completed" for e in events):
            if decision_only:
                raise CodexHostActionUnknown('incomplete decision event stream; host effects require readback',
                    details={'source':'codex-cli','log_dir':str(log_dir) if log_dir else None})
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
