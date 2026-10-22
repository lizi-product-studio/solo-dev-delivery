#!/usr/bin/env python3
"""Source-first local delivery gate and Codex Stop adapter. No model or scheduler.

This enforces fresh execution of the host's selected business scenarios. It does
not prove semantic completeness, authenticate reviewers, or sandbox host code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import challenge
from core.coverage import coverage_report, digest, discover_evidence, source_brief, validate_plan
from core.local_checks import canonical, load_json


def read(path):
    return load_json(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(canonical(value) + '\n')
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


def start(workspace, inputs, outputs, task_path, session=None, home=None):
    root = Path(workspace).resolve(strict=True)
    task_path = challenge.outside(task_path, root)
    if task_path.exists() or not outputs or len(set(inputs + outputs)) != len(inputs + outputs):
        raise ValueError('new task and distinct inputs/outputs required')
    brief = source_brief(root, inputs, split_items=True)
    challenge.write_new(task_path.parent / 'brief.json', brief)
    task = {'format': 'local-delivery-gate/1', 'workspace': str(root),
            'inputs': inputs, 'outputs': outputs, 'baseline': baseline(root, outputs),
            'brief': str(task_path.parent / 'brief.json'), 'state': 'NEEDS_SOURCE_CHECKS',
            'history': [], 'session': session}
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
            'next': 'Main agent: derive real business chain and distinguishing boundary scenarios from these sources; write external unittest file and coverage plan, then seal before implementation.'}


def seal(task_path, plan_path, reason=None):
    task_path, plan_path = Path(task_path).resolve(strict=True), Path(plan_path).resolve(strict=True)
    task, plan = read(task_path), read(plan_path)
    if task.get('plan') and not (isinstance(reason, str) and reason.strip()):
        raise ValueError('amending checks requires a source-based reason; old failures stay in history')
    if not task.get('plan') and baseline(Path(task['workspace']), task['outputs']) != task['baseline']:
        raise ValueError('acceptance must be sealed before implementation changes; start from the original candidate')
    brief = read(task['brief'])
    if brief != source_brief(task['workspace'], task['inputs'], split_items=True):
        raise ValueError('source brief differs from original documents')
    if not challenge.current({'workspace': task['workspace'], 'inputs': brief['inputs'], 'outputs': []}):
        raise ValueError('source changed; create a new source-first task')
    if validate_plan(brief, plan):
        raise ValueError('unaccounted source units: ' + ', '.join(sorted(validate_plan(brief, plan))))
    # Every selected business outcome must have an actual test; one chain test
    # can cover many outcomes. No arbitrary scenario count or extra agent.
    covered = {x for c in plan['checks'] for x in c['covers']}
    if covered != {o['id'] for o in plan['outcomes']}:
        raise ValueError('every business outcome needs an executable check')
    test = challenge.outside(Path(plan['test_file']).resolve(strict=True), Path(task['workspace']))
    challenge.outside(plan_path, Path(task['workspace']))
    snapshot = {'plan': digest(plan_path), 'test': digest(test), 'brief': digest(task['brief']), 'reason': reason or 'source-first acceptance'}
    # Immutable copy prevents an amendment from erasing what was accepted before.
    number = len(task['history'])
    version = task_path.parent / ('scope-' + str(number))
    version.mkdir()
    (version / 'plan.json').write_bytes(plan_path.read_bytes())
    (version / 'checks.py').write_bytes(test.read_bytes())
    task['history'].append(snapshot)
    task.update(plan=str(plan_path), sealed=snapshot, state='CHECKS_SEALED')
    save(task_path, task)
    return {'status': 'CHECKS_SEALED', 'outcomes': len(plan['outcomes']), 'checks': len(plan['checks'])}


def check(task_path, execute=False):
    task_path = Path(task_path).resolve(strict=True)
    task = read(task_path)
    if task.get('format') != 'local-delivery-gate/1':
        raise ValueError('not a delivery task')
    if not task.get('plan'):
        return {'status': 'BLOCKED', 'next_action': 'SELECT_SOURCE_BUSINESS_SCENARIOS', 'brief': task['brief']}
    sealed = task['sealed']
    if any(digest(item['path']) != item for item in [sealed['plan'], sealed['test'], sealed['brief']]):
        return {'status': 'BLOCKED', 'next_action': 'RECONCILE_CHANGED_ACCEPTANCE_WITH_SOURCE'}
    packet = challenge.candidate_packet(task['workspace'], task['inputs'], task['outputs'])
    key = hashlib.sha256(canonical({'packet': packet, 'plan': sealed['plan'], 'test': sealed['test'], 'brief': sealed['brief']}).encode()).hexdigest()[:24]
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
    sufficient = result['status'] == 'DECLARED_SCOPE_COVERED'
    if execute:
        task.update(state='VERIFIED' if sufficient else 'CHECKING', last_report=str(report_path))
        save(task_path, task)
    return {'status': 'DELIVERABLE_WITHIN_DECLARED_SCOPE' if sufficient else 'BLOCKED',
            'next_action': 'STOP' if sufficient else result['next_action'],
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
        return hook(json.load(sys.stdin), os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    if args.op == 'install-hook':
        return install_hook(args.home)
    if args.op == 'start':
        return start(args.workspace, args.input, args.output, args.task, args.session)
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
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if 'status' not in result or result['status'] in ('NEEDS_SOURCE_CHECKS', 'CHECKS_SEALED', 'DELIVERABLE_WITHIN_DECLARED_SCOPE', 'HOOK_PREPARED_REQUIRES_HOST_TRUST') else 1)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc)}))
        sys.exit(2)
