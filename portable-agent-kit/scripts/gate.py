#!/usr/bin/env python3
"""Source-first local delivery gate and Codex Stop adapter. No model or scheduler.

This enforces fresh execution of the host's selected business scenarios. It does
not prove semantic completeness, authenticate reviewers, or sandbox host code.
Control JSON and trusted Python source use UTF-8 independently of host locale.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import sys

import challenge
from core.coverage import coverage_report, digest, discover_evidence, source_brief, validate_plan
from core.local_checks import artifact, canonical, load_json
from core.decisions import compile_plan


def read(path):
    return load_json(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(canonical(value) + '\n', encoding='utf-8')
    temporary.replace(path)


def registry(home, session):
    if not isinstance(session, str) or not session:
        raise ValueError('session ID required for Codex registration')
    return Path(home) / 'delivery-gates' / (hashlib.sha256(session.encode()).hexdigest() + '.json')


def baseline(root, names):
    values = []
    for name in names:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not name:
            raise ValueError('outputs must be relative paths inside workspace')
        target = (root / path).resolve()
        if root not in target.parents:
            raise ValueError('output escapes workspace')
        values.append({'path': name, 'sha256': digest(target)['sha256'] if target.exists() else None})
    return values


def _support_artifacts(root, names, inputs, outputs):
    """Bind explicitly selected dependencies without making them requirements."""
    if not isinstance(names, (list, tuple)):
        raise ValueError('support files must be an explicit list of relative files')
    if len(inputs) + len(outputs) + len(names) > 200:
        raise ValueError('declare at most 200 files; use native project review for larger scopes')
    occupied = [(root / name).resolve() for name in inputs + outputs]
    entries = []
    for name in names:
        if (not isinstance(name, str) or not name or Path(name).is_absolute()
                or PureWindowsPath(name).drive or name.startswith(('\\', '/'))
                or '..' in Path(name).parts or '..' in PureWindowsPath(name).parts):
            raise ValueError('support files must be relative paths without traversal')
        path = root
        for part in Path(name).parts:
            path = path / part
            # lstat also detects dangling links; reparse points include Windows
            # junctions on Python versions without Path.is_junction().
            attributes = getattr(path.lstat(), 'st_file_attributes', 0)
            if path.is_symlink() or attributes & 0x400:
                raise ValueError('support files must not traverse links or junctions')
        target = path.resolve(strict=True)
        if any(target == other or target in other.parents or other in target.parents
               for other in occupied):
            raise ValueError('support files must not duplicate or overlap inputs, outputs or each other')
        entries.append(artifact(root, name))
        occupied.append(target)
    return entries


def _support_current(task):
    try:
        expected = task.get('support_files', [])
        if task.get('plan') and expected != task['sealed'].get('support_files', []):
            return False
        if not expected:
            return True
        return _support_artifacts(Path(task['workspace']), [item['path'] for item in expected],
                                  task['inputs'], task['outputs']) == expected
    except (OSError, ValueError, KeyError, TypeError):
        return False


def start(workspace, inputs, outputs, task_path, session=None, home=None, decision_mode=True, support_files=None):
    root = Path(workspace).resolve(strict=True)
    task_path = challenge.outside(task_path, root)
    if task_path.exists() or not outputs or len(set(inputs + outputs)) != len(inputs + outputs):
        raise ValueError('new task and distinct inputs/outputs required')
    support = _support_artifacts(root, support_files, inputs, outputs) if support_files is not None else []
    brief = source_brief(root, inputs, split_items=True, split_statements=decision_mode)
    challenge.write_new(task_path.parent / 'brief.json', brief)
    task = {'format': 'local-delivery-gate/2' if decision_mode else 'local-delivery-gate/1', 'workspace': str(root),
            'inputs': inputs, 'outputs': outputs, 'baseline': baseline(root, outputs),
            'brief': str(task_path.parent / 'brief.json'), 'state': 'NEEDS_SOURCE_CHECKS',
            'history': [], 'session': session}
    if support:
        task['support_files'] = support
    if session:
        reg = registry(home or os.environ.get('CODEX_HOME', str(Path.home() / '.codex')), session)
        if reg.exists():
            previous = read(reg)
            if Path(previous['task']).exists() and check(previous['task'], False)['status'] != 'DELIVERABLE_WITHIN_DECLARED_SCOPE':
                raise ValueError('session already has an unfinished delivery gate')
        task['registration'] = str(reg)
    challenge.write_new(task_path, task)
    if session:
        save(reg, {'task': str(task_path), 'workspace': str(root)})
    return {'status': task['state'], 'task': str(task_path), 'source_units': brief['units'],
            'next': 'Main agent: write real journey checks from every source statement before implementation; checks entries can directly name sources, without duplicate outcomes/covers. Add a small scalar decision model only where independent conditions, thresholds or priority are not distinguished by those checks; seal generates those missing cases. Do not model every action or duplicate persisted-state tests. Observe Path.cwd()/SOLO_CANDIDATE_ROOT, never the original workspace.'}


def source_checks(brief, plan):
    """Reuse exact raw requirements instead of asking the host to rewrite them."""
    if not any('sources' in c for c in plan.get('checks', [])):
        return plan
    units = {u['id']: u for u in brief['units']}
    outcomes = list(plan.get('outcomes', []))
    inferred, checks = set(), []
    for check in plan['checks']:
        check = dict(check)
        if 'sources' in check:
            sources = check.pop('sources')
            if (not isinstance(sources, list) or not sources or not set(sources) <= set(units)
                    or 'covers' in check):
                raise ValueError('check.sources must name real source units; do not also write covers')
            check['covers'] = ['source@' + x for x in sources]
            for source in sources:
                if source not in inferred:
                    outcomes.append({'id': 'source@' + source, 'sources': [source], 'expected': units[source]['text']})
                    inferred.add(source)
        checks.append(check)
    return dict(plan, outcomes=outcomes, checks=checks)


def _absolute_literals(text):
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ValueError("adapter syntax error: " + str(exc)) from exc
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and (node.value.startswith("/") or PureWindowsPath(node.value).is_absolute())]


def _resolved_path(value):
    if os.name != 'nt' and PureWindowsPath(value).is_absolute() and not Path(value).is_absolute():
        # A foreign Windows path must not become a relative POSIX workspace path.
        return PureWindowsPath(value)
    if os.name == 'nt':
        # Extended paths name the same files but retain their prefix in resolve().
        value = str(PureWindowsPath(value))
        if value[:8].lower() == '\\\\?\\unc\\':
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value).resolve()


def seal(task_path, plan_path, reason=None):
    task_path, plan_path = Path(task_path).resolve(strict=True), Path(plan_path).resolve(strict=True)
    task, plan = read(task_path), read(plan_path)
    if not _support_current(task):
        raise ValueError('support files changed or disappeared; create a new source-first task')
    if task.get('plan') and not (isinstance(reason, str) and reason.strip()):
        raise ValueError('amending checks requires a source-based reason; old failures stay in history')
    if not task.get('plan') and baseline(Path(task['workspace']), task['outputs']) != task['baseline']:
        raise ValueError('acceptance must be sealed before implementation changes; start from the original candidate')
    brief = read(task['brief'])
    if brief != source_brief(task['workspace'], task['inputs'], split_items=True, split_statements=brief.get('granularity') == 'statement'):
        raise ValueError('source brief differs from original documents')
    if not challenge.current({'workspace': task['workspace'], 'inputs': brief['inputs'], 'outputs': []}):
        raise ValueError('source changed; create a new source-first task')
    test = challenge.outside(Path(plan['test_file']).resolve(strict=True), Path(task['workspace']))
    challenge.outside(plan_path, Path(task['workspace']))
    generated, selection = None, None
    original_plan = plan
    if task['format'] == 'local-delivery-gate/2':
        plan = source_checks(brief, plan)
    if task['format'] == 'local-delivery-gate/2':
        workspace = _resolved_path(task['workspace'])
        if any(path == workspace or workspace in path.parents
               for path in (_resolved_path(value) for value in _absolute_literals(test.read_text(encoding='utf-8')))):
            raise ValueError('adapter references original workspace; use Path.cwd() or SOLO_CANDIDATE_ROOT to observe the frozen candidate copy')
        if plan.get('decisions'):
            plan, generated, selection = compile_plan(brief, plan, test.read_text(encoding='utf-8'))
    if validate_plan(brief, plan):
        raise ValueError('unaccounted source units: ' + ', '.join(sorted(validate_plan(brief, plan))))
    covered = {x for c in plan['checks'] for x in c['covers']}
    if covered != {o['id'] for o in plan['outcomes']}:
        raise ValueError('every business outcome needs an executable check')
    # Freeze source, policy, adapter and generated observations as one revision.
    # Old /1 tasks keep their original path and behavior.
    number = len(task['history'])
    version = task_path.parent / ('scope-' + str(number))
    version.mkdir()
    (version / 'plan.json').write_bytes(plan_path.read_bytes())
    (version / 'checks.py').write_bytes(test.read_bytes())
    snapshot = {'plan': digest(plan_path), 'test': digest(test), 'brief': digest(task['brief']),
                'reason': reason or 'source-first acceptance'}
    if task.get('support_files'):
        snapshot['support_files'] = task['support_files']
    active_plan = plan_path
    if generated is not None:
        generated_path = version / 'generated-checks.py'
        generated_path.write_text(generated, encoding='utf-8')
        selection_path = version / 'selection.json'
        save(selection_path, selection)
        plan['test_file'] = str(generated_path)
        active_plan = version / 'compiled-plan.json'
        save(active_plan, plan)
        snapshot.update(adapter=digest(test), test=digest(generated_path),
                        compiled=digest(active_plan), selection=digest(selection_path))
    elif plan is not original_plan:
        active_plan = version / 'compiled-plan.json'
        save(active_plan, plan)
        snapshot['compiled'] = digest(active_plan)
    task['history'].append(snapshot)
    task.update(plan=str(active_plan), sealed=snapshot, state='CHECKS_SEALED')
    save(task_path, task)
    return {'status': 'CHECKS_SEALED', 'outcomes': len(plan['outcomes']), 'checks': len(plan['checks']),
            'selection': [{'id': x['id'], 'reachable_inputs': x['product_size'],
                           'required_distinctions': len(x['obligations']), 'selected_cases': len(x['cases'])}
                          for x in (selection or [])]}


def check(task_path, execute=False):
    task_path = Path(task_path).resolve(strict=True)
    task = read(task_path)
    if task.get('format') not in ('local-delivery-gate/1', 'local-delivery-gate/2'):
        raise ValueError('not a delivery task')
    if not _support_current(task):
        return {'status': 'BLOCKED', 'next_action': 'RESTART_WITH_CHANGED_SUPPORT_FILES'}
    if not task.get('plan'):
        return {'status': 'BLOCKED', 'next_action': 'SELECT_SOURCE_BUSINESS_SCENARIOS', 'brief': task['brief']}
    sealed = task['sealed']
    if any(digest(item['path']) != item for item in [sealed[k] for k in ('plan', 'test', 'brief', 'adapter', 'compiled', 'selection') if k in sealed]):
        return {'status': 'BLOCKED', 'next_action': 'RECONCILE_CHANGED_ACCEPTANCE_WITH_SOURCE'}
    execution_inputs = task['inputs'] + [item['path'] for item in task.get('support_files', [])]
    packet = challenge.candidate_packet(task['workspace'], execution_inputs, task['outputs'])
    if task['format'] == 'local-delivery-gate/2':
        packet['execution_scope'] = 'candidate-copy'
    binding = {'packet': packet, 'plan': sealed['plan'], 'test': sealed['test'], 'brief': sealed['brief']}
    if 'compiled' in sealed:
        binding['compiled'] = sealed['compiled']
    key = hashlib.sha256(canonical(binding).encode()).hexdigest()[:24]
    control = task_path.parent / 'runs' / key
    packet_path = control / 'packet.json'
    if not packet_path.exists():
        challenge.write_new(packet_path, packet)
    # Existing assessor selects only missing scenarios and reuses valid evidence.
    # A fresh FAIL dominates PASS for the same candidate and acceptance bytes.
    index = len(list(control.glob('assessment-*.json')))
    report_path = control / ('assessment-' + str(index) + '.json')
    if execute:
        result = challenge.assess(task['brief'], task['plan'], packet_path, [], 12800, True, report_path)
    else:
        result = coverage_report(task['brief'], task['plan'], packet_path,
                                 discover_evidence([control]), 12800, challenge.status, challenge.current)
        result['executed_check_ids'] = []
    support_current = _support_current(task)
    sufficient = result['status'] == 'DECLARED_SCOPE_COVERED' and support_current
    if execute:
        task.update(state='VERIFIED' if sufficient else 'CHECKING', last_report=str(report_path))
        save(task_path, task)
    return {'status': 'DELIVERABLE_WITHIN_DECLARED_SCOPE' if sufficient else 'BLOCKED',
            'next_action': ('RESTART_WITH_CHANGED_SUPPORT_FILES' if not support_current
                            else 'STOP' if sufficient else result['next_action']),
            'missing_outcomes': result['missing_outcomes'], 'missing_checks': result['missing_required_checks'],
            'failed_outcomes': result['failed_outcomes'], 'related_outcomes_to_review': result['related_outcomes_to_review'],
            'executed_checks': result['executed_check_ids'], 'packet': str(packet_path),
            'report': str(report_path) if execute else task.get('last_report'),
            'limits': 'Host must judge source interpretation and assertion quality. This is local declared-scope acceptance, not production or customer acceptance.'}


def hook(payload, home):
    if payload.get('hook_event_name') != 'Stop':
        return {}
    reg = registry(home, payload.get('session_id'))
    if not reg.exists():
        return {}
    task_path = '<unreadable registration>'
    try:
        registration = read(reg)
        task_path = registration['task']
        # Session IDs scope registration. Cwd is deliberately not used as a
        # bypass: the agent can change cwd while the same delivery is open.
        task = read(task_path)
        if task.get('state') == 'BLOCKED_EXTERNAL':
            if digest(task['blocker']['evidence']['path']) != task['blocker']['evidence']:
                raise ValueError('blocked-state evidence changed')
            return {'systemMessage': 'Delivery remains BLOCKED, not complete: ' + task['blocker']['reason']}
        result = check(task_path, False)
        if result['status'] == 'DELIVERABLE_WITHIN_DECLARED_SCOPE':
            task = read(task_path)
            task['state'] = 'DELIVERED'
            save(task_path, task)
            reg.unlink()
            return {}
        detail = json.dumps(result, ensure_ascii=False)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        detail = 'gate evidence unavailable: ' + str(exc)
    # stop_hook_active never exempts an unresolved task. No timed worker kills.
    return {'decision': 'block', 'reason': 'Delivery is not verified. Resolve the smallest source/evidence gap below; do not claim completion. Run gate.py check --task ' + shlex.quote(str(task_path)) + ' --run after implementing/checking. If user input or external access is truly required, state that blocker without claiming delivery. ' + detail}


def install_hook(home):
    """Prepare hook definition; host trust remains the user's security decision."""
    target = Path(home) / 'hooks.json'
    config = read(target) if target.exists() else {}
    hooks = config.setdefault('hooks', {})
    groups = hooks.setdefault('Stop', [])
    command = 'python3 ' + shlex.quote(str(Path(__file__).resolve())) + ' hook'
    if not any(h.get('command') == command for g in groups for h in g.get('hooks', [])):
        groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]})
        save(target, config)
    return {'status': 'HOOK_PREPARED_REQUIRES_HOST_TRUST', 'path': str(target),
            'command': command, 'next': 'Review and trust this exact hook definition in Codex /hooks. Installation alone is not activation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    p = commands.add_parser('start')
    p.add_argument('--workspace', required=True, type=Path)
    p.add_argument('--input', required=True, action='append')
    p.add_argument('--support-file', action='append', help='explicit read-only runtime/test dependency; copied into the candidate, not interpreted as a requirement')
    p.add_argument('--output', required=True, action='append')
    p.add_argument('--task', required=True, type=Path)
    p.add_argument('--session', default=os.environ.get('CODEX_THREAD_ID'))
    p = commands.add_parser('seal')
    p.add_argument('--task', required=True, type=Path)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--reason')
    p = commands.add_parser('check')
    p.add_argument('--task', required=True, type=Path)
    p.add_argument('--run', action='store_true')
    p = commands.add_parser('blocked', help='allow a truthful blocked handoff; never marks delivery complete')
    p.add_argument('--task', required=True, type=Path)
    p.add_argument('--reason', required=True)
    p.add_argument('--evidence', required=True, type=Path)
    p = commands.add_parser('install-hook')
    p.add_argument('--home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    commands.add_parser('hook')
    args = parser.parse_args()
    if args.op == 'hook':
        return hook(json.loads(sys.stdin.buffer.read().decode('utf-8')),
                    os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    if args.op == 'install-hook':
        return install_hook(args.home)
    if args.op == 'start':
        return start(args.workspace, args.input, args.output, args.task, args.session, support_files=args.support_file)
    if args.op == 'seal':
        return seal(args.task, args.plan, args.reason)
    if args.op == 'blocked':
        if not args.reason.strip():
            raise ValueError('specific external blocker required')
        task = read(args.task)
        task.update(state='BLOCKED_EXTERNAL', blocker={'reason': args.reason, 'evidence': digest(args.evidence)})
        save(args.task, task)
        return {'status': 'BLOCKED', 'delivery_complete': False, 'reason': args.reason}
    return check(args.task, args.run)


if __name__ == '__main__':
    try:
        result = main()
        # ASCII escapes are UTF-8-valid JSON even on a legacy stdio locale.
        print(json.dumps(result, ensure_ascii=True))
        sys.exit(0 if 'status' not in result or result['status'] in ('NEEDS_SOURCE_CHECKS', 'CHECKS_SEALED', 'DELIVERABLE_WITHIN_DECLARED_SCOPE', 'HOOK_PREPARED_REQUIRES_HOST_TRUST') else 1)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc)}))
        sys.exit(2)
