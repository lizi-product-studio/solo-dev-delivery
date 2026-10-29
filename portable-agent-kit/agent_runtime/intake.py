"""Compile an untrusted task plan within immutable host authority.

Planning is not acceptance. The generated contract always requires a frozen,
host-frozen review script (supplied or independently prepared) in addition to mechanical artifact checks.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from control_plane.contracts import canonical_hash, normalize_contract


class IntakeError(ValueError):
    pass


MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_FILES = 200


def relative_path(value, *, allow_root=False):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise IntakeError('path must be a nonempty workspace-relative string')
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts):
        raise IntakeError('path must remain inside workspace')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM' + str(n) for n in range(1, 10)} | {'LPT' + str(n) for n in range(1, 10)}
    if any(part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved for part in path.parts):
        raise IntakeError('artifact path uses an ambiguous or reserved filename')
    result = path.as_posix()
    if result == '.' and not allow_root:
        raise IntakeError('artifact path must name a file')
    return result


def checked_path(workspace, value):
    root = Path(workspace)
    name = relative_path(value)
    cursor = root
    parts = PurePosixPath(name).parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        if cursor.is_symlink() or getattr(cursor, 'is_junction', lambda: False)():
            raise IntakeError('symbolic links and junctions are not task artifacts')
        if index < len(parts) - 1 and cursor.exists() and not cursor.is_dir():
            raise IntakeError('artifact parent must be a directory')
    target = (root / name).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise IntakeError('artifact escapes workspace') from None
    return target


def fingerprint(workspace, name):
    target = checked_path(workspace, name)
    if not target.is_file() or target.stat().st_size > MAX_ARTIFACT_BYTES:
        raise IntakeError('input must be an existing file of at most 5 MiB: ' + name)
    data = target.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise IntakeError('input grew beyond 5 MiB')
    return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}


def build_context(goal, workspace, allow_write, authorization_ref, *, max_steps=16):
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 65536:
        raise IntakeError('goal must be nonempty and at most 65536 characters')
    if not isinstance(authorization_ref, str) or not authorization_ref.strip():
        raise IntakeError('host authorization_ref is required')
    if type(max_steps) is not int or not 1 <= max_steps <= 1000:
        raise IntakeError('max_steps must be 1..1000')
    root = Path(workspace).absolute()
    if root.is_symlink() or getattr(root, 'is_junction', lambda: False)():
        raise IntakeError('workspace must not be a symbolic link or junction')
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise IntakeError('workspace must be an existing directory')
    if not isinstance(allow_write, list) or not allow_write:
        raise IntakeError('host allow_write paths are required')
    authority = []
    for value in allow_write:
        name = relative_path(value, allow_root=True)
        target = root if name == '.' else checked_path(root, name)
        directory = name == '.' or value.endswith(('/', '\\')) or target.is_dir()
        authority.append({'path': name, 'directory': directory})

    # Never follow links or scan unbounded trees. Small previews help the one-shot
    # planner name real inputs; tools inspect the actual files during execution.
    files = []
    pending = [root]
    visited = 0
    preview_left = 32768
    truncated = False
    while pending and visited < 1000 and len(files) < MAX_FILES:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 1000 or len(files) >= MAX_FILES:
                        truncated = True
                        break
                    if entry.name.startswith('.') or entry.name in {'node_modules', '__pycache__'}:
                        continue
                    path = Path(entry.path)
                    if entry.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    row = {'path': path.relative_to(root).as_posix(), 'bytes': entry.stat().st_size}
                    if row['bytes'] <= MAX_ARTIFACT_BYTES:
                        row.update(fingerprint(root, row['path']))
                        if preview_left > 0:
                            data = path.read_bytes()[:min(8192, preview_left)]
                            try:
                                text = data.decode('utf-8-sig')
                                if '\x00' not in text:
                                    row['preview'] = text
                                    row['preview_truncated'] = row['bytes'] > len(data)
                                    preview_left -= len(data)
                            except UnicodeError:
                                pass
                    files.append(row)
        except OSError as exc:
            raise IntakeError('cannot inventory authorized workspace: ' + str(exc)) from exc
    return {'format': 'goal-intake-context/1', 'goal': goal,
            'goal_sha256': hashlib.sha256(goal.encode('utf-8')).hexdigest(),
            'workspace': str(root), 'authorization_ref': authorization_ref,
            'allow_write': authority, 'max_steps': max_steps,
            'files': files, 'inventory_truncated': truncated or bool(pending)}


def planner_request(context, task_id):
    return {'task_id': task_id, 'goal': context['goal'], 'step': 1,
            'memory': [], 'skills': [], 'observations': [],
            'tools': {'submit_plan': {
                'description': 'Submit a plan only; this tool performs no task action. '
                    'inputs are existing read-only reference files; outputs are editable file paths. '
                    'steps are bounded execution instructions. unknowns contain only necessary '
                    'missing business decisions, never facts that execution can calculate. '
                    'Do not submit authorization, acceptance, reviewer, policy or budget fields.',
                'arguments': '(inputs: list[str], outputs: list[str], steps: list[str], unknowns: list[str])'}},
            'planning_context': {'files': context['files'],
                                 'host_services':context.get('host_services',{}),
                                 'inventory_truncated': context['inventory_truncated'],
                                 'host_allow_write': context['allow_write'],
                                 'external_actions': False}}


def validate_plan(proposal, context):
    required = {'inputs', 'outputs', 'steps', 'unknowns'}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise IntakeError('plan must contain exactly inputs, outputs, steps and unknowns')
    plan = {}
    for key in required:
        values = proposal[key]
        if not isinstance(values, list) or len(values) > (MAX_FILES if key in {'inputs', 'outputs'} else 32):
            raise IntakeError('invalid or excessive plan ' + key)
        if any(not isinstance(value, str) or not value.strip() or len(value) > 4096 for value in values):
            raise IntakeError('plan ' + key + ' must contain nonempty bounded strings')
        plan[key] = [relative_path(value) for value in values] if key in {'inputs', 'outputs'} else list(values)
    if not plan['outputs'] or not plan['steps'] or len(plan['inputs']) + len(plan['outputs']) > MAX_FILES:
        raise IntakeError('plan needs outputs and steps, and at most 200 artifacts')
    def comparison(value):
        return os.path.normcase(value).replace('\\', '/')
    # Editing an existing file naturally makes it both a planning input and an
    # output. Duplicates within either list remain malformed; cross-list overlap
    # is normalized only after every output has passed host authority checks.
    for key in ('inputs', 'outputs'):
        names = [comparison(value) for value in plan[key]]
        if len(set(names)) != len(names):
            raise IntakeError('duplicate artifact in plan ' + key)
    previous = {row['path']: row.get('sha256') for row in context['files']}
    for name in plan['inputs']:
        current = fingerprint(context['workspace'], name)
        if previous.get(name) and previous[name] != current['sha256']:
            raise IntakeError('input changed after planning context: ' + name)
    for name in plan['outputs']:
        target = checked_path(context['workspace'], name)
        if target.exists() and not target.is_file():
            raise IntakeError('output must name a file: ' + name)
        permitted = any(row['path'] == '.' or comparison(name) == comparison(row['path'])
                        or (row['directory'] and comparison(name).startswith(comparison(row['path']) + '/'))
                        for row in context['allow_write'])
        if not permitted:
            raise IntakeError('planned output exceeds host authorization: ' + name)
    editable = {comparison(name) for name in plan['outputs']}
    plan['inputs'] = [name for name in plan['inputs'] if comparison(name) not in editable]
    return plan


def compile_contract(context, proposal, *, task_id, review_sha256, planner_steps):
    plan = validate_plan(proposal, context)
    if plan['unknowns']:
        raise IntakeError('necessary business decisions remain unknown')
    if (not isinstance(review_sha256, str) or len(review_sha256) != 64
            or any(char not in '0123456789abcdef' for char in review_sha256)):
        raise IntakeError('a frozen trusted review hash is required')
    if type(planner_steps) is not int or planner_steps not in (0, 1):
        raise IntakeError('planner_steps must be 0 or 1')
    execution_steps = context['max_steps'] - planner_steps
    if execution_steps < 1:
        raise IntakeError('planning exhausted the model decision budget')
    sources = [fingerprint(context['workspace'], name) for name in plan['inputs']]
    initial_outputs = [fingerprint(context['workspace'], name) if checked_path(context['workspace'], name).is_file()
                       else {'path': name, 'missing': True} for name in plan['outputs']]
    binding = {'goal_sha256': context['goal_sha256'], 'plan_sha256': canonical_hash(plan),
               'inputs': sources, 'initial_outputs': initial_outputs, 'review_sha256': review_sha256}
    return normalize_contract({
        'task_id': task_id, 'goal': context['goal'], 'owner': 'agent-runtime',
        'candidate': 'intake-' + canonical_hash(binding),
        'authorization_ref': context['authorization_ref'],
        'scope': plan['outputs'], 'inputs': plan['inputs'],
        'excluded': ['external writes', 'production actions', 'read-only inputs', 'authorization changes'],
        'policy': {'workspace_write': True, 'shell_required': False, 'external_actions': False,
                   'ux_required': False, 'require_input_reads': True},
        # CP counts start and automatic finish in addition to tool results. Its
        # mutation budget does not grant additional model decisions.
        'budget': {'max_steps': execution_steps + 2, 'model_steps': execution_steps, 'max_retries': 2},
        'required_review_sha256': review_sha256,
        'intake': {'format': 'goal-intake/1', **binding,
                   'plan': plan, 'plan_is_advisory': True,
                   'total_decision_budget': context['max_steps'], 'planner_steps': planner_steps,
                   'execution_decision_budget': execution_steps,
                   'limits': 'Structural checks require the separately frozen trusted business review.'},
        'acceptance': [{'id': 'artifact-' + str(index + 1), 'expected': 'Declared artifact exists: ' + name,
                        'environment': 'local', 'assertion': {'type': 'file_exists', 'path': name}}
                       for index, name in enumerate(plan['outputs'])]})
