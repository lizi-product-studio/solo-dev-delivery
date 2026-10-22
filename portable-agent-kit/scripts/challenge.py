#!/usr/bin/env python3
"""Freeze a local candidate, replay reviewer unittest cases, and detect stale results.

No model, network or scheduler. The supplied Python test is trusted host code,
executed against a temporary copy of declared files. This is NOT a security
sandbox, independent-identity authentication or a production acceptance gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.local_checks import artifact, canonical, file_bytes, load_json, pointer, precise_json, run_check

LIMIT = 65536
MARKER = 'SOLO_CHALLENGE_RESULT='
HARNESS = '''import importlib.util, json, os, sys, unittest
# Binding protects against accidental tests of the original workspace rather
# than the frozen copy. Trusted test code can bypass this; it is not a sandbox.
source_root = os.environ.get('SOLO_SOURCE_WORKSPACE')
if source_root:
    source_root = os.path.normcase(os.path.realpath(source_root))
    def bound_path(value):
        if isinstance(value, (list, tuple)):
            return any(bound_path(x) for x in value)
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        value = os.path.normcase(os.fsdecode(value))
        resolved = os.path.normcase(os.path.realpath(value)) if os.path.isabs(value) else value
        return source_root in value or resolved == source_root or resolved.startswith(source_root + os.sep)
    def candidate_binding(event, args):
        values = args[:3] if event == 'subprocess.Popen' else args[:1]
        if event in ('open', 'os.chdir', 'os.listdir', 'os.scandir', 'subprocess.Popen', 'os.system') and bound_path(values):
            raise PermissionError('Acceptance must observe the frozen candidate copy; use Path.cwd() or SOLO_CANDIDATE_ROOT, not the original workspace')
    sys.addaudithook(candidate_binding)
spec = importlib.util.spec_from_file_location("independent_challenge", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.defaultTestLoader.loadTestsFromModule(module)
def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from flatten(item)
        else: yield item
def key(test):
    name = test.id()
    prefix = 'independent_challenge.'
    return name[len(prefix):] if name.startswith(prefix) else name
selected = json.loads(sys.argv[2])
if selected:
    available = {key(test):test for test in flatten(suite)}
    if not set(selected) <= set(available):
        print('Requested test IDs not discovered: '+str(sorted(set(selected)-set(available))), file=sys.stderr)
        suite = unittest.TestSuite()
    else: suite = unittest.TestSuite(available[name] for name in dict.fromkeys(selected))
class Observed(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.cases[key(test)] = 'UNKNOWN'
    def addSuccess(self, test):
        super().addSuccess(test)
        if self.cases[key(test)] != 'FAIL': self.cases[key(test)] = 'PASS'
    def addFailure(self, test, err):
        super().addFailure(test, err); self.cases[key(test)] = 'FAIL'
    def addError(self, test, err):
        super().addError(test, err); self.cases[key(test)] = 'FAIL'
    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test); self.cases[key(test)] = 'FAIL'
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None: self.cases[key(test)] = 'FAIL'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.cases = {}
result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr, resultclass=Observed).run(suite)
summary = {"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),"expected_failures":len(result.expectedFailures),"unexpected_successes":len(result.unexpectedSuccesses)}
summary['cases'] = [{'id':name,'status':status} for name,status in result.cases.items()]
print("SOLO_CHALLENGE_RESULT=" + json.dumps(summary), flush=True)
sys.exit(0 if result.wasSuccessful() and result.testsRun and not (result.skipped or result.expectedFailures) else 1)
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(canonical(data) + '\n')


def outside(path, workspace):
    path = Path(path).resolve()
    if path == workspace or workspace in path.parents:
        raise ValueError('control, receipt and reviewer test must be outside candidate workspace')
    return path


def current(packet):
    try:
        root = Path(packet['workspace']).resolve(strict=True)
        return all(artifact(root, entry['path']) == entry for entry in packet['inputs'] + packet['outputs'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def candidate_packet(workspace, inputs, outputs):
    workspace = Path(workspace).resolve(strict=True)
    if not inputs or not outputs or len(set(inputs + outputs)) != len(inputs + outputs):
        raise ValueError('need distinct, nonempty input and output file lists')
    if len(inputs) + len(outputs) > 200:
        raise ValueError('declare at most 200 files; use native project review for larger scopes')
    return {'format': 'local-challenge/1', 'workspace': str(workspace),
              'inputs': [artifact(workspace, p) for p in inputs],
              'outputs': [artifact(workspace, p) for p in outputs],
              'review_instruction': 'Derive critical outcomes from original inputs before reading implementation/self-tests. Write unittest scenarios using real actions, persisted readback and contradictory evidence where relevant. Mark untested business semantics honestly.',
              'limits': 'No identity or authorization authentication; no proof of complete coverage or production readiness.'}


def freeze(workspace, inputs, outputs, target):
    packet = candidate_packet(workspace, inputs, outputs)
    target = outside(target, Path(packet['workspace']))
    write_new(target, packet)
    return {'status': 'FROZEN', 'packet': str(target), 'packet_sha256': sha(target.read_bytes()), **packet}


def tail(path):
    with path.open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - LIMIT))
        data = stream.read(LIMIT)
    return {'text': data.decode('utf-8', errors='replace'), 'bytes': size, 'truncated': size > LIMIT}


def run(packet_path, test_path, receipt_path, timeout, selected=None, patches=None):
    packet_path = Path(packet_path).resolve(strict=True)
    packet_raw = packet_path.read_bytes()
    packet = load_json(packet_raw.decode('utf-8'))
    root = Path(packet['workspace']).resolve(strict=True)
    test_path = outside(test_path, root).resolve(strict=True)
    receipt_path = outside(receipt_path, root)
    if receipt_path.exists():
        raise ValueError('receipt exists; preserve earlier findings and use a new receipt')
    if not 1 <= timeout <= 300:
        raise ValueError('test subprocess timeout must be between 1 and 300 seconds')
    selected = selected or []
    if not isinstance(selected, list) or not all(isinstance(name, str) and name for name in selected):
        raise ValueError('selected test IDs must be nonempty strings')
    if not current(packet):
        raise ValueError('STALE_CANDIDATE: freeze current candidate before executing')
    test_bytes = test_path.read_bytes()
    if len(test_bytes) > LIMIT or test_path.suffix != '.py':
        raise ValueError('reviewer test must be a Python file of at most 64 KiB')
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix='solo-challenge-') as td:
        scratch = Path(td)
        work = scratch / 'candidate'
        work.mkdir()
        for entry in packet['inputs'] + packet['outputs']:
            data = file_bytes(root, entry['path'])
            if sha(data) != entry['sha256']:
                raise ValueError('candidate changed during copy')
            dest = work / entry['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if patches:
            outputs = {entry['path'] for entry in packet['outputs']}
            for change in patches:
                if change['path'] not in outputs:
                    raise ValueError('counterfactual may replace declared output code only')
                target = work / change['path']
                if target.suffix != '.py':
                    raise ValueError('counterfactual patch must target Python code, not source policies or data')
                before, after = change['before'], change['after']
                text = target.read_text()
                if not before or before == after or text.count(before) != 1:
                    raise ValueError('counterfactual needs one exact, nontrivial replacement')
                text = text.replace(before, after, 1)
                compile(text, str(target), 'exec')
                target.write_text(text)
        test = scratch / 'review_checks.py'
        test.write_bytes(test_bytes)
        stdout_path, stderr_path = scratch / 'stdout', scratch / 'stderr'
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(work),
               'SOLO_CANDIDATE_ROOT': str(work)}
        env.pop('SOLO_SOURCE_WORKSPACE', None)
        if packet.get('execution_scope') == 'candidate-copy':
            env['SOLO_SOURCE_WORKSPACE'] = str(root)
        with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
            proc = subprocess.Popen([sys.executable, '-B', '-c', HARNESS, str(test), json.dumps(selected)], cwd=work,
                                    env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                # This stops only this disposable test process group, never an Agent.
                os.killpg(proc.pid, signal.SIGKILL)
                code = proc.wait()
        stdout, stderr = tail(stdout_path), tail(stderr_path)
    summaries = [line[len(MARKER):] for line in stdout['text'].splitlines() if line.startswith(MARKER)]
    summary = json.loads(summaries[-1]) if summaries else None
    test_cases = summary.pop('cases', []) if summary else []
    status = 'UNKNOWN'
    if not timed_out and summary and summary['tests'] > 0:
        if summary['failures'] or summary['errors'] or summary['unexpected_successes']:
            status = 'FAIL'
        elif not summary['skipped'] and not summary['expected_failures'] and code == 0:
            status = 'CHALLENGE_PASS'
    try:
        unchanged = current(packet) and packet_path.read_bytes() == packet_raw and test_path.read_bytes() == test_bytes
    except OSError:
        unchanged = False
    if not unchanged:
        status = 'STALE'
    receipt = {'format': 'local-counterfactual-result/1' if patches else 'local-challenge-result/1', 'status': status,
               'packet': str(packet_path), 'packet_sha256': sha(packet_raw),
               'test': str(test_path), 'test_sha256': sha(test_bytes),
               'exit_code': code, 'timed_out': timed_out,
               'elapsed_seconds': round(time.monotonic() - started, 4),
               'test_summary': summary, 'test_cases': test_cases, 'selected_test_ids': selected,
               'stdout': stdout, 'stderr': stderr,
               'independent_identity_authenticated': False,
               'limits': 'Observed local unittest results only. Host verifies reviewer independence and business coverage. Not a sandbox, publication or production acceptance.'}
    if patches:
        receipt['counterfactual_patches'] = patches
    write_new(receipt_path, receipt)
    return receipt


def verify(workspace, inputs, outputs, test_path, receipt_path, timeout=60, probes=None):
    from core.sensitivity import selected_probes, verify_sensitive
    probes = probes or selected_probes(workspace, inputs, outputs, test_path, receipt_path)
    if probes is not None:
        return verify_sensitive(workspace, inputs, outputs, test_path, receipt_path,
                                timeout, probes, _verify_suite, run, status, write_new)
    return _verify_suite(workspace, inputs, outputs, test_path, receipt_path, timeout)


def _verify_suite(workspace, inputs, outputs, test_path, receipt_path, timeout=60):
    """One invocation for ordinary local acceptance; preserve full evidence on disk.

    Reuse is only within this isolated control directory, for identical declared
    candidate bytes and current test bytes. A partial PASS cannot stand for the
    full suite; a known current FAIL wins. No claim of semantic sufficiency.
    """
    packet = candidate_packet(workspace, inputs, outputs)
    root = Path(packet['workspace'])
    test_path = outside(test_path, root).resolve(strict=True)
    receipt_path = outside(receipt_path, root)
    if not 1 <= timeout <= 300:
        raise ValueError('test subprocess timeout must be between 1 and 300 seconds')
    from core.coverage import discover_evidence
    reusable = []
    for path in discover_evidence([receipt_path.parent]):
        value = load_json(Path(path).read_text())
        if value.get('test') != str(test_path) or value.get('test_sha256') != sha(test_path.read_bytes()):
            continue
        try:
            same = load_json(Path(value['packet']).read_text()) == packet
            observed = status(path)['status'] if same else 'STALE'
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if observed == 'FAIL' or (observed == 'CHALLENGE_PASS'
                                and value.get('selected_test_ids') == []
                                and value.get('test_cases')):
            reusable.append((observed != 'FAIL', path, value))
    if reusable:
        _, actual_path, receipt = sorted(reusable, key=lambda x: (x[0], x[1]))[0]
        reused = True
    else:
        if receipt_path.exists():
            raise ValueError('receipt exists but is not reusable; preserve it and choose a new report')
        packet_path = receipt_path.with_name(receipt_path.stem + '-packet.json')
        if packet_path.exists():
            if load_json(packet_path.read_text()) != packet:
                raise ValueError('packet exists for other bytes; preserve it and choose a new report')
        else:
            write_new(packet_path, packet)
        receipt = run(packet_path, test_path, receipt_path, timeout)
        actual_path, reused = str(receipt_path), False
    summary = {'status': receipt['status'], 'receipt': actual_path, 'reused': reused,
               'executed_tests': 0 if reused else (receipt.get('test_summary') or {}).get('tests', 0),
               'test_summary': receipt.get('test_summary'),
               'failed_tests': [c['id'] for c in receipt.get('test_cases', []) if c['status'] == 'FAIL'],
               'limits': 'Observed suite only; host decides business sufficiency and stop. No independent identity or production proof.'}
    if receipt['status'] not in ('CHALLENGE_PASS',):
        summary['diagnostics'] = receipt.get('stderr', {}).get('text', '')[-3000:]
    return summary


def status(receipt_path):
    receipt = load_json(Path(receipt_path).read_text())
    if receipt.get('format') == 'local-sensitivity-result/1':
        from core.sensitivity import fresh
        valid = fresh(receipt, status)
        return {'status': receipt['status'] if valid else 'STALE',
                'recorded_status': receipt['status'], 'limits': receipt['limits']}
    if receipt.get('format') == 'local-coverage-result/1':
        from core.coverage import fresh_report
        valid = fresh_report(receipt, current, status)
        return {'status': receipt['status'] if valid else 'STALE',
                'recorded_status': receipt['status'], 'limits': receipt['limits']}
    if receipt.get('format') == 'local-relations-result/1':
        try:
            spec_path = Path(receipt['spec'])
            spec = load_json(spec_path.read_text())
            valid = sha(spec_path.read_bytes()) == receipt['spec_sha256']
            valid = valid and all(artifact(receipt['workspace'], x['path']) == x for x in receipt['artifacts'])
            fresh = [{'id': c['id'], **run_check(receipt['workspace'], c, spec['scope'])} for c in spec['checks']]
            valid = valid and fresh == receipt['checks']
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        return {'status': receipt['status'] if valid else 'STALE',
                'recorded_status': receipt['status'], 'limits': receipt['limits']}
    try:
        packet_path = Path(receipt['packet'])
        valid = sha(packet_path.read_bytes()) == receipt['packet_sha256']
        packet = load_json(packet_path.read_text())
        valid = valid and current(packet) and sha(Path(receipt['test']).read_bytes()) == receipt['test_sha256']
    except (OSError, ValueError, KeyError):
        valid = False
    return {'status': receipt['status'] if valid else 'STALE',
            'recorded_status': receipt['status'], 'limits': receipt['limits']}


def check_relations(workspace, spec_path, receipt_path):
    root = Path(workspace).resolve(strict=True)
    spec_path = outside(spec_path, root).resolve(strict=True)
    receipt_path = outside(receipt_path, root)
    raw = spec_path.read_bytes()
    spec = load_json(raw.decode('utf-8'))
    checks, scope = spec.get('checks'), spec.get('scope')
    if not isinstance(scope, list) or not scope or not all(isinstance(x, str) for x in scope):
        raise ValueError('spec.scope must declare artifact paths')
    if not isinstance(checks, list) or not checks or not all(isinstance(x, dict) and isinstance(x.get('id'), str) and x['id'] for x in checks):
        raise ValueError('spec.checks must contain checks with nonempty IDs')
    if len({x['id'] for x in checks}) != len(checks):
        raise ValueError('check IDs must be unique')
    observed = [{'id': c['id'], **run_check(root, c, scope)} for c in checks]
    all_artifacts = [x for c in observed for x in c['artifacts']]
    artifacts = {x['path']: x for x in all_artifacts}
    verdict = 'RELATIONS_PASS' if all(x['verdict'] == 'PASS' for x in observed) else 'FAIL' if any(x['verdict'] == 'FAIL' for x in observed) else 'UNKNOWN'
    try:
        # Every observation must describe the same current candidate. Collapsing
        # by path before checking would hide edits between two checks.
        unchanged = spec_path.read_bytes() == raw and all(artifact(root, x['path']) == x for x in all_artifacts)
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        verdict = 'STALE'
    receipt = {'format': 'local-relations-result/1', 'workspace': str(root),
               'status': verdict, 'spec': str(spec_path), 'spec_sha256': sha(raw),
               'checks': observed, 'artifacts': list(artifacts.values()),
               'limits': 'Only declared artifact relations. This neither proves source correctness, free prose semantics nor full business acceptance.'}
    write_new(receipt_path, receipt)
    return receipt


def render(workspace, source, rows, columns, block, output):
    from core.json_relations import block_markers, render_table
    root = Path(workspace).resolve(strict=True)
    values = pointer(precise_json(file_bytes(root, source).decode('utf-8-sig')), rows)
    begin, end = block_markers(block)
    text = begin + '\n' + render_table(values, columns) + '\n' + end + '\n'
    if Path(output).is_absolute() or '..' in Path(output).parts:
        raise ValueError('render output must be workspace relative')
    target = (root / output).resolve()
    if root not in target.parents or target == (root / source).resolve():
        raise ValueError('output escapes workspace or replaces source')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(text)
    return {'status': 'RENDERED', 'artifact': artifact(root, output),
            'limits': 'Only this generated block reflects the structured rows; free prose still needs review.'}


def brief(workspace, inputs, target):
    from core.coverage import source_brief
    root = Path(workspace).resolve(strict=True)
    target = outside(target, root)
    result = source_brief(root, inputs)
    write_new(target, result)
    return {'status': 'SOURCE_BRIEF', 'brief': str(target), **result}


def assess(brief_path, plan_path, packet_path, evidence, budget, execute, report_path):
    from core.coverage import coverage_report, digest, discover_evidence
    packet = load_json(Path(packet_path).read_text())
    root = Path(packet['workspace']).resolve(strict=True)
    report_path = outside(report_path, root)
    if report_path.exists():
        raise ValueError('report exists; preserve previous result and use a new report')
    for path in (brief_path, plan_path, packet_path):
        outside(path, root)
    plan = load_json(Path(plan_path).read_text())
    outside(plan['test_file'], root)
    evidence = list(evidence) + discover_evidence([report_path.parent])
    result = coverage_report(brief_path, plan_path, packet_path, evidence, budget, status, current)
    original_dependencies = result['dependencies']
    executed = []
    if execute and result['next_action'] == 'RUN_SELECTED_CHECKS':
        replay = report_path.with_name(report_path.stem + '-replay.json')
        executed = result['selected_checks']
        run(packet_path, plan['test_file'], replay, 60, [c['test'] for c in executed])
        evidence = list(evidence) + [str(replay)]
        try:
            result = coverage_report(brief_path, plan_path, packet_path, evidence, budget, status, current)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Actual execution already occurred: retain that receipt even if its
            # basis disappeared or became invalid during replay.
            result['status'] = 'STALE'
            result['next_action'] = 'RECONCILE_CHANGED_BASIS'
            result['evaluation_error'] = str(exc)
            result['evidence'] = evidence
            result['dependencies'].append(digest(replay))
    result['executed_check_ids'] = [c['id'] for c in executed]
    result['executed_effort_units'] = sum(c.get('cost', 1) for c in executed)
    result['control_directories'] = sorted(set(result['control_directories'] + [str(report_path.parent)]))
    # Keep observations from before execution too; edits during a replay cannot
    # replace the basis with a newer plan or silently drop an earlier receipt.
    try:
        if any(digest(d['path']) != d for d in original_dependencies):
            result['status'] = 'STALE'
            result['next_action'] = 'RECONCILE_CHANGED_BASIS'
    except OSError:
        result['status'] = 'STALE'
        result['next_action'] = 'RECONCILE_CHANGED_BASIS'
    result['dependencies'] += original_dependencies
    write_new(report_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    p = commands.add_parser('verify', help='freeze and run a chosen local suite once; reuse current evidence; compact output')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--input', action='append', required=True)
    p.add_argument('--output', action='append', required=True)
    p.add_argument('--test', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--probes', type=Path, help='optional critical-risk counterfactuals; surviving wrong behavior blocks sufficiency')
    p.add_argument('--timeout', type=int, default=60, help='disposable local test only; not an Agent time limit')
    p = commands.add_parser('freeze')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--input', action='append', required=True)
    p.add_argument('--output', action='append', required=True)
    p.add_argument('--packet', type=Path, required=True)
    p = commands.add_parser('run')
    p.add_argument('--packet', type=Path, required=True)
    p.add_argument('--test', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--timeout', type=int, default=60, help='disposable local test only; not an Agent time limit')
    p.add_argument('--select', action='append', help='Class.test_method; omitted means all discovered cases')
    p = commands.add_parser('status')
    p.add_argument('--receipt', type=Path, required=True)
    p = commands.add_parser('check')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p = commands.add_parser('render')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--source', required=True)
    p.add_argument('--rows', type=json.loads, required=True, help='JSON pointer array')
    p.add_argument('--columns', type=json.loads, required=True, help='array of label/pointer objects')
    p.add_argument('--block', required=True)
    p.add_argument('--output', required=True, help='new workspace-relative markdown file')
    p = commands.add_parser('brief', help='account for original goal/policy paragraphs before choosing checks')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--input', action='append', required=True)
    p.add_argument('--brief', type=Path, required=True)
    p = commands.add_parser('assess', help='find coverage gaps, reuse evidence and optionally run a bounded selected suite')
    p.add_argument('--brief', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--packet', type=Path, required=True)
    p.add_argument('--evidence', action='append', default=[])
    p.add_argument('--budget', type=int, default=12800, help='relative local check effort, not model quota or Agent timeout')
    p.add_argument('--run', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    args = vars(parser.parse_args())
    op = args.pop('op')
    if op == 'verify':
        return verify(args['workspace'], args['input'], args['output'], args['test'], args['report'], args['timeout'], args['probes'])
    if op == 'freeze':
        return freeze(args['workspace'], args['input'], args['output'], args['packet'])
    if op == 'run':
        return run(args['packet'], args['test'], args['receipt'], args['timeout'], args['select'])
    if op == 'status':
        return status(args['receipt'])
    if op == 'check':
        return check_relations(args['workspace'], args['spec'], args['receipt'])
    if op == 'brief':
        return brief(args['workspace'], args['input'], args['brief'])
    if op == 'assess':
        return assess(args['brief'], args['plan'], args['packet'], args['evidence'], args['budget'], args['run'], args['report'])
    return render(**args)


if __name__ == '__main__':
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result['status'] in ('FROZEN', 'CHALLENGE_PASS', 'SENSITIVITY_PASS', 'RELATIONS_PASS', 'RENDERED', 'SOURCE_BRIEF', 'DECLARED_SCOPE_COVERED') else 1)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'status': 'ERROR', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
