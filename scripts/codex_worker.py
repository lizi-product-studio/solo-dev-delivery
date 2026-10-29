"""Synchronous, subscription-only Codex CLI adapter; never applies patches or queues work."""
import hashlib
import contextlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from review_engine import FAMILIES


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


_STRING = {'type': 'string', 'minLength': 1}
_STRINGS = {'type': 'array', 'items': _STRING}
OUTPUT_SCHEMA = _object({
    'status': {'type': 'string', 'enum': ['PASS', 'BLOCKED', 'FINDINGS']},
    'summary': _STRING,
    'failure_kind': {'type': 'string', 'enum': ['none', 'reasoning', 'environment', 'requirements', 'capacity']},
    'observations': {'type': 'array', 'items': _object({'check_id': _STRING, 'observed': _STRING})},
    'findings': {'type': 'array', 'items': _object({
        'severity': {'type': 'string', 'enum': ['P0', 'P1', 'P2', 'P3']},
        'family': {'type': 'string', 'enum': sorted(FAMILIES)},
        'components': dict(_STRINGS, minItems=1), 'description': _STRING, 'reproduction': _STRING})},
    'resolved_findings': _STRINGS, 'uncovered': _STRINGS,
})


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _validate(value, schema=OUTPUT_SCHEMA):
    kind = schema['type']
    _require(isinstance(value, {'object': dict, 'array': list, 'string': str}[kind]), 'Invalid result type')
    if kind == 'object':
        _require(set(value) == set(schema['required']), 'Missing or additional result fields')
        for key, child in schema['properties'].items():
            _validate(value[key], child)
    elif kind == 'array':
        _require(len(value) >= schema.get('minItems', 0), 'Empty result list')
        for child in value:
            _validate(child, schema['items'])
    else:
        _require(bool(value.strip()), 'Empty result string')
        _require('enum' not in schema or value in schema['enum'], 'Unknown result enum')


def _json(data):
    def pairs(items):
        value = {}
        for key, item in items:
            _require(key not in value, 'Duplicate JSON key')
            value[key] = item
        return value
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def _env():
    # Preserve CLI authentication, but never pass API keys or arbitrary parent secrets.
    keep = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT', 'CODEX_HOME')
    return dict({key: os.environ[key] for key in keep if key in os.environ},
                GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')


def _git(repo, *args, data=None, git_dir=None):
    argv = ['git', '-C', str(repo), '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.attributesFile=' + os.devnull, '-c', 'core.autocrlf=false',
            '-c', 'commit.gpgSign=false']
    if git_dir:
        argv += ['--git-dir=' + str(git_dir), '--work-tree=' + str(repo)]
    return subprocess.run(argv + list(args), input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=_env(), check=True, timeout=60).stdout


def _snapshot(repository, candidate, workspace, control):
    _require(_git(repository, 'rev-parse', 'HEAD').decode().strip() == candidate,
             'Candidate must be the current committed HEAD')
    entries = []
    for record in _git(repository, 'ls-tree', '-rz', '--full-tree', candidate).split(b'\0'):
        if not record:
            continue
        info, raw_path = record.split(b'\t', 1)
        mode, kind, oid = info.decode().split()
        path = Path(os.fsdecode(raw_path))
        _require(kind == 'blob' and mode in ('100644', '100755', '120000'),
                 'Submodules or unsupported Git entries require an explicit environment adapter')
        _require(not path.is_absolute() and '..' not in path.parts and '.git' not in path.parts,
                 'Unsafe snapshot path')
        entries.append((mode, oid, path))
    blobs = _git(repository, 'cat-file', '--batch', data=''.join(x[1] + '\n' for x in entries).encode())
    workspace.mkdir()
    offset = 0
    for mode, oid, path in entries:
        end = blobs.index(b'\n', offset)
        actual, kind, size = blobs[offset:end].decode().split()
        _require(actual == oid and kind == 'blob', 'Invalid Git blob')
        content = blobs[end + 1:end + 1 + int(size)]
        offset = end + 2 + int(size)
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == '120000':
            link = os.fsdecode(content)
            _require(not Path(link).is_absolute() and (target.parent / link).resolve().is_relative_to(workspace),
                     'Snapshot symlink escapes workspace')
            target.symlink_to(link)
        else:
            target.write_bytes(content)
            target.chmod(0o755 if mode == '100755' else 0o644)
    object_format = _git(repository, 'rev-parse', '--show-object-format').decode().strip()
    _git(workspace, 'init', '--template=', '--object-format=' + object_format)
    _git(workspace, 'add', '--all', '--force', '.')
    _require(_git(workspace, 'write-tree').strip() == _git(repository, 'rev-parse', candidate + '^{tree}').strip(),
             'Git attributes changed the snapshot; an explicit checkout adapter is required')
    # Preserve candidate identity without copying repository configuration or ancestor objects.
    raw_commit = _git(repository, 'cat-file', 'commit', candidate)
    baseline = _git(workspace, 'hash-object', '-t', 'commit', '-w', '--stdin', data=raw_commit).decode().strip()
    _require(baseline == candidate, 'Candidate commit identity did not survive snapshot export')
    _git(workspace, 'update-ref', 'refs/heads/worker-snapshot', candidate)
    _git(workspace, 'symbolic-ref', 'HEAD', 'refs/heads/worker-snapshot')
    (workspace / '.git' / 'shallow').write_text(candidate + '\n')
    shutil.copytree(workspace / '.git', control)
    return baseline


def _fingerprint(root):
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            mode = path.lstat().st_mode
            _require(stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode), 'Unsupported workspace file')
            data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else b''
            result[str(path.relative_to(root))] = (mode, hashlib.sha256(data).hexdigest())
    return result


def _events(path, receipt):
    events = [_json(line) for line in path.read_text().splitlines() if line.strip()]
    _require(events and all(isinstance(e, dict) and isinstance(e.get('type'), str) for e in events),
             'Missing or malformed JSONL events')
    starts = [e for e in events if e['type'] == 'thread.started']
    _require(len(starts) == 1 and events[0] == starts[0] and isinstance(starts[0].get('thread_id'), str)
             and starts[0]['thread_id'].strip(), 'Missing or invalid thread.started')
    receipt['thread_id'] = starts[0]['thread_id']
    completed = [e for e in events if e['type'] == 'turn.completed']
    _require(len(completed) == 1 and events[-1] == completed[0]
             and not any(e['type'] in ('turn.failed', 'error') for e in events), 'Turn did not complete cleanly')
    usage = completed[0].get('usage')
    if usage is not None:
        _require(isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                 for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')), 'Malformed usage')
        _require(usage['cached_input_tokens'] <= usage['input_tokens'], 'Invalid cached token count')
    receipt['usage'] = usage


def _stop(process):
    """Stop only the owned worker tree and retain uncertainty for the caller."""
    result = {'method': 'windows-taskkill-tree' if os.name == 'nt' else 'posix-process-group',
              'status': 'FAILED', 'leader_stopped': False, 'tree_stopped': False, 'errors': []}
    if os.name == 'nt':
        try:
            system_root = os.environ.get('SYSTEMROOT') or os.environ.get('WINDIR')
            if not system_root:
                raise OSError('SYSTEMROOT and WINDIR are unavailable; cannot locate taskkill.exe')
            command = [str(Path(system_root) / 'System32' / 'taskkill.exe'),
                       '/PID', str(process.pid), '/T', '/F']
            stopped = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode:
                detail = (stopped.stdout + stopped.stderr).decode(errors='replace')[:1000]
                result['errors'].append('taskkill exited ' + str(stopped.returncode) + ': ' + detail)
        except (OSError, subprocess.SubprocessError) as exc:
            result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:1000])
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:1000])
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        except (OSError, subprocess.SubprocessError) as exc:
            result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:1000])
        finally:
            # Descendants may outlive a TERM-compliant group leader.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:1000])
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        result['errors'].append('worker wait failed: ' + type(exc).__name__ + ': ' + str(exc)[:1000])
    result['leader_stopped'] = process.poll() is not None
    result['tree_stopped'] = result['leader_stopped'] and not result['errors']
    result['status'] = 'COMPLETE' if result['tree_stopped'] else 'FAILED'
    return result


class ParentHandoff(Exception):
    def __init__(self, decision):
        self.decision = decision
        super().__init__(decision.get("reason", "Explicit parent handoff"))


def _monitored_wait(process, instructions, stdout, progress_path, timeout, observation_seconds,
                    supervise=None, context=None):
    """Time creates a parent decision request. It NEVER terminates the worker.

    The parent may renew the checkpoint for the SAME process, or explicitly
    request adjust/takeover. No response leaves the child running.
    """
    started = last_activity = time.monotonic()
    offset = 0
    pending = b''
    active_tools = set()
    completed_items = failures = 0
    first = True
    report = {}
    next_checkpoint = started + timeout
    last_decision = started
    decision_sequence = acknowledged_failures = 0
    control_error = None
    while True:
        now = time.monotonic()
        try:
            with stdout.open('rb') as source:
                source.seek(offset)
                fresh = source.read()
                offset = source.tell()
        except FileNotFoundError:
            fresh = b''
        if fresh:
            last_activity = now
            pending += fresh
            lines = pending.split(b'\n')
            pending = lines.pop()
            # A malformed/oversize event is not accepted as a completed item.
            if len(pending) > 8_000_000:
                pending = b''
            for line in lines:
                try:
                    event = json.loads(line)
                    item = event.get('item', {})
                    if not isinstance(item, dict):
                        continue
                    iid = item.get('id')
                    if not isinstance(iid, str):
                        continue
                    if event.get('type') == 'item.started' and item.get('type') in (
                            'command_execution', 'mcp_tool_call', 'web_search'):
                        active_tools.add(iid)
                    if event.get('type') == 'item.completed':
                        active_tools.discard(iid)
                        completed_items += 1
                        if item.get('status') == 'failed' or item.get('exit_code') not in (None, 0):
                            failures += 1
                except (ValueError, AttributeError, TypeError):
                    pass
        elapsed = now - started
        quiet = now - last_activity
        reasons = []
        if now >= next_checkpoint:
            reasons.append('PLANNED_TIME_REACHED')
        if quiet >= observation_seconds and now-last_decision >= observation_seconds:
            reasons.append('QUIET_REQUIRES_ASSESSMENT')
        if failures > acknowledged_failures:
            reasons.append('TOOL_FAILURE_REQUIRES_ASSESSMENT')
        running = process.poll() is None
        state = ('DECISION_REQUIRED' if reasons else
                 'WAITING_TOOL' if active_tools else 'ACTIVITY_OBSERVED') if running else 'PROCESS_EXITED'
        report = {**(context or {}), 'protocol':3, 'state':state, 'observed_at':time.time(),
                  'elapsed_seconds':round(elapsed,3), 'quiet_seconds':round(quiet,3),
                  'active_tools':len(active_tools), 'completed_items':completed_items, 'failed_items':failures,
                  'decision_sequence':decision_sequence, 'needs_decision':bool(reasons) and running,
                  'decision_reasons':reasons if running else [], 'process_running':running,
                  'observation_seconds':observation_seconds,
                  'next_checkpoint_seconds':round(next_checkpoint-started,3),
                  'events_file':str(stdout), 'event_bytes_observed':offset, 'control_error':control_error,
                  'limit':'Time and silence request parent assessment; neither stops the process.'}
        tmp = progress_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, progress_path)
        if not running:
            if supervise:
                try: supervise(report)
                except Exception: pass
            return report
        command = None
        if supervise:
            try:
                command = supervise(report)
                control_error = None
            except Exception as exc:
                # Losing the control channel is not permission to kill healthy work.
                control_error = type(exc).__name__ + ': ' + str(exc)[:200]
        if command:
            action = command.get('action')
            sequence = command.get('sequence')
            if action == 'authorization_revoked':
                raise ParentHandoff(command)
            if type(sequence) is int and sequence > decision_sequence:
                if action == 'continue':
                    interval = command.get('next_checkpoint_seconds')
                    _require(type(interval) in (int,float) and math.isfinite(interval) and interval > 0,
                             'Invalid parent checkpoint interval')
                    decision_sequence = sequence
                    next_checkpoint = time.monotonic() + interval
                    last_decision = time.monotonic()
                    acknowledged_failures = failures
                elif action in ('adjust','takeover'):
                    decision_sequence = sequence
                    report.update(decision_sequence=sequence,state='HANDOFF_REQUESTED')
                    try:
                        supervise(report)  # Acknowledge receipt; running lock remains until actual stop.
                    except Exception:
                        pass
                    raise ParentHandoff(command)
        try:
            process.communicate(instructions if first else None,
                                timeout=min(1.0, observation_seconds / 2))
            first = False
        except subprocess.TimeoutExpired:
            first = False


def run_worker(*, executable, repository, candidate, role, model, effort, prompt,
               output_dir, timeout, on_start, launch_guard=None, observation_seconds=None, supervise=None):
    """Return validated business fields plus host evidence; execution faults are BLOCKED.

    on_start(child_pid) runs immediately after Popen. No real model is used in unit tests.
    Unknown token usage is None. Callers own approvals, finding registration and patch application.
    An implement PASS is only a patch handoff; it never constitutes quality acceptance.
    """
    _require(role in ('implement', 'explore', 'review'), 'Unknown worker role')
    _require(isinstance(candidate, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', candidate), 'Full candidate hash required')
    _require(all(isinstance(x, str) and x.strip() and '\0' not in x for x in (str(executable), model, effort, prompt)),
             'Executable, model, effort and prompt are required')
    _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and math.isfinite(timeout)
             and timeout > 0 and (on_start is None or callable(on_start)), 'Invalid timeout or callback')
    _require(observation_seconds is None or (type(observation_seconds) in (int,float)
             and math.isfinite(observation_seconds) and observation_seconds > 0), 'Invalid observation interval')
    observation_seconds = observation_seconds if observation_seconds is not None else 300
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='worker-', dir=destination))
    workspace, control = root / 'workspace', root / 'baseline.git'
    stdout, stderr, result_path = root / 'events.jsonl', root / 'stderr.log', root / 'result.json'
    schema_path = root / 'output-schema.json'
    schema_path.write_text(json.dumps(OUTPUT_SCHEMA))
    receipt = {'status': 'BLOCKED', 'summary': 'Worker did not complete', 'failure_kind': 'environment',
               'observations': [], 'findings': [], 'resolved_findings': [], 'uncovered': [],
               'execution_status': 'ERROR', 'requested_model': model, 'effort': effort, 'role': role,
               'candidate': candidate, 'snapshot_commit': None, 'workspace': str(workspace),
               'output_dir': str(root), 'child_pid': None, 'thread_id': None, 'usage': None,
               'returncode': None, 'patch': None, 'changed_paths': [], 'logs': {}, 'process_running': False}
    process, started = None, time.monotonic()
    if observation_seconds is not None:
        receipt['supervision'] = {'observation_seconds':observation_seconds, 'initial_checkpoint_seconds':timeout,
                                  'progress_file':str(destination/'progress.json')}
    try:
        receipt['snapshot_commit'] = _snapshot(Path(repository).resolve(), candidate, workspace, control)
        before = _fingerprint(workspace) if role != 'implement' else None
        executable_path = Path(str(executable))
        # Windows CreateProcess cannot execute a shebang-only synthetic or
        # adapter script directly (WinError 193).  Preserve the host-provided
        # executable for real binaries, but run a plain script through the
        # current Python interpreter so test doubles and local adapters behave
        # the same way on POSIX and Windows.
        launcher = [sys.executable, str(executable_path)] if (
            os.name == 'nt' and executable_path.is_file()
            and executable_path.suffix.lower() not in ('.exe', '.com', '.bat', '.cmd')
        ) else [str(executable)]
        argv = launcher + ['exec', '--json', '--ephemeral', '--ignore-user-config',
                '--output-schema', str(schema_path), '-m', model, '-c', 'model_reasoning_effort=' + json.dumps(effort),
                '-c', 'agents.enabled=false', '-c', 'forced_login_method="chatgpt"', '-c', 'approval_policy="never"',
                '-c', 'project_doc_max_bytes=0', '--cd', str(workspace), '--sandbox',
                'workspace-write' if role == 'implement' else 'read-only', '-o', str(result_path), '-']
        instructions = ('Work only in this isolated repository. Read applicable repository AGENTS.md. '
                        'Never delegate, spawn agents, run nested Codex, read user credentials/configuration, '
                        'write outside this repository, or access production. Report missing prerequisites honestly. '
                        'Implement PASS means a patch handoff awaiting host verification; list unrun checks in uncovered. '
                        'Review and explore PASS require no uncovered checks. '
                        'Return only the required JSON. Role: ' + role + '.\n' + prompt)
        instructions += ('\nInitial parent progress checkpoint: '+str(timeout)+' seconds; this is NOT a stop deadline. '
                         'The parent inspects progress and may continue the same process or explicitly request a handoff. '
                         'Complete the authorized bounded work; do not self-stop merely because planned time elapsed.')
        with stdout.open('wb') as out, stderr.open('wb') as err:
            with launch_guard() if launch_guard else contextlib.nullcontext():
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                           cwd=workspace, env=_env(), start_new_session=True)
                receipt['child_pid'] = process.pid
                if on_start:
                    on_start(process.pid)
            _monitored_wait(process, instructions.encode(), stdout, destination/'progress.json',
                            timeout, observation_seconds, supervise,
                            {'candidate':candidate,'workspace':str(workspace),'child_pid':process.pid})
        receipt['returncode'] = process.returncode
        _events(stdout, receipt)
        _require(process.returncode == 0, 'Codex CLI exited unsuccessfully')
        answer = _json(result_path.read_text())
        _validate(answer)
        receipt.update(answer, execution_status='COMPLETED')
        try:
            _require(answer['status'] != 'PASS' or (answer['failure_kind'] == 'none' and not answer['findings']
                     and (role == 'implement' or not answer['uncovered'])),
                     'PASS contradicts reported failure/findings/uncovered checks for this role')
            _require(answer['status'] != 'BLOCKED' or answer['failure_kind'] != 'none', 'BLOCKED needs failure_kind')
            _require(answer['status'] != 'FINDINGS' or bool(answer['findings']), 'FINDINGS needs findings')
        except ValueError as exc:
            # Transport and schema are verified; retain evidence, but never accept a contradictory answer.
            receipt.update(status='BLOCKED', failure_kind='reasoning', summary='Worker result semantics: ' + str(exc))
    except ParentHandoff as exc:
        receipt.update(summary='Explicit parent decision: '+exc.decision['action']+'; preserve work for handoff',
                       execution_status='PARENT_HANDOFF', failure_kind='requirements',
                       parent_decision=exc.decision)
    except Exception as exc:
        receipt['summary'] = type(exc).__name__ + ': ' + str(exc)[:600]
    finally:
        if process is not None:
            if process.poll() is None or receipt['execution_status'] != 'COMPLETED':
                try:
                    receipt['process_cleanup'] = _stop(process)
                except Exception as exc:
                    receipt['process_cleanup'] = {'status': 'FAILED', 'method': 'unexpected-cleanup-error',
                        'leader_stopped': process.poll() is not None, 'tree_stopped': False,
                        'errors': [type(exc).__name__ + ': ' + str(exc)[:1000]]}
            cleanup = receipt.get('process_cleanup')
            stop_failed = bool(cleanup and not cleanup['tree_stopped'])
            receipt['process_running'] = True if process.poll() is None else (None if stop_failed else False)
            if stop_failed:
                receipt.update(status='BLOCKED', execution_status='ERROR', failure_kind='environment',
                               summary=receipt['summary'] + '; worker tree stop unconfirmed: ' + '; '.join(cleanup['errors']))
            receipt['returncode'] = process.returncode
            if process.poll() is not None and process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError as exc:
                    receipt['stdin_close_error'] = type(exc).__name__ + ': ' + str(exc)[:1000]
            if receipt['execution_status'] != 'COMPLETED' and stdout.exists():
                try:
                    _events(stdout, receipt)
                except (OSError, ValueError):
                    pass
        stopped = receipt['process_running'] is False
        if stopped and role != 'implement' and receipt['snapshot_commit']:
            try:
                after = _fingerprint(workspace)
                receipt['changed_paths'] = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
                _require(not receipt['changed_paths'], 'Read-only worker mutated its snapshot')
            except (OSError, ValueError) as exc:
                receipt.update(status='BLOCKED', failure_kind='environment', execution_status='ERROR', summary=str(exc))
        if stopped and role == 'implement' and receipt['snapshot_commit']:
            try:
                _git(workspace, 'add', '--all', '--force', '.', git_dir=control)
                patch_path = root / 'changes.patch'
                patch_path.write_bytes(_git(workspace, 'diff', '--cached', '--binary', '--no-ext-diff',
                                            '--no-textconv', receipt['snapshot_commit'], '--', git_dir=control))
                receipt['patch'] = {'path': str(patch_path), 'sha256': hashlib.sha256(patch_path.read_bytes()).hexdigest()}
                receipt['changed_paths'] = [os.fsdecode(p) for p in _git(workspace, 'diff', '--cached',
                    '--name-only', '-z', receipt['snapshot_commit'], '--', git_dir=control).split(b'\0') if p]
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                receipt.update(status='BLOCKED', failure_kind='environment', execution_status='ERROR',
                               summary='Patch capture failed: ' + type(exc).__name__)
        for key, path in (('stdout', stdout), ('stderr', stderr), ('result', result_path), ('schema', schema_path)):
            if path.is_file() and not path.is_symlink():
                receipt['logs'][key] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        receipt['seconds'] = round(time.monotonic() - started, 3)
        if observation_seconds is not None and (destination/'progress.json').exists():
            try:
                progress = json.loads((destination/'progress.json').read_text(encoding='utf-8'))
            except (OSError, ValueError, UnicodeError):
                progress = {'workspace': str(workspace), 'child_pid': receipt['child_pid']}
            progress.update(state=receipt['execution_status'], observed_at=time.time(),
                            process_running=receipt['process_running'], needs_decision=not stopped,
                            decision_reasons=[] if stopped else ['PROCESS_STOP_UNCONFIRMED'],
                            handoff_workspace=str(workspace), patch=receipt.get('patch'))
            if receipt.get('process_cleanup'):
                progress['process_cleanup'] = receipt['process_cleanup']
            try:
                (destination/'progress.json').write_text(json.dumps(progress, ensure_ascii=False), encoding='utf-8')
            except OSError as exc:
                receipt['progress_error'] = type(exc).__name__ + ': ' + str(exc)[:1000]
        (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return receipt
