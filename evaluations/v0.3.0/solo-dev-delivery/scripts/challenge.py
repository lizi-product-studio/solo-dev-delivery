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
HARNESS = '''import importlib.util, json, sys, unittest
spec = importlib.util.spec_from_file_location("independent_challenge", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.defaultTestLoader.loadTestsFromModule(module)
result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)
summary = {"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),"expected_failures":len(result.expectedFailures),"unexpected_successes":len(result.unexpectedSuccesses)}
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


def freeze(workspace, inputs, outputs, target):
    workspace = Path(workspace).resolve(strict=True)
    target = outside(target, workspace)
    if not inputs or not outputs or len(set(inputs + outputs)) != len(inputs + outputs):
        raise ValueError('need distinct, nonempty input and output file lists')
    if len(inputs) + len(outputs) > 200:
        raise ValueError('declare at most 200 files; use native project review for larger scopes')
    packet = {'format': 'local-challenge/1', 'workspace': str(workspace),
              'inputs': [artifact(workspace, p) for p in inputs],
              'outputs': [artifact(workspace, p) for p in outputs],
              'review_instruction': 'Derive critical outcomes from original inputs before reading implementation/self-tests. Write unittest scenarios using real actions, persisted readback and contradictory evidence where relevant. Mark untested business semantics honestly.',
              'limits': 'No identity or authorization authentication; no proof of complete coverage or production readiness.'}
    write_new(target, packet)
    return {'status': 'FROZEN', 'packet': str(target), 'packet_sha256': sha(target.read_bytes()), **packet}


def tail(path):
    with path.open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - LIMIT))
        data = stream.read(LIMIT)
    return {'text': data.decode('utf-8', errors='replace'), 'bytes': size, 'truncated': size > LIMIT}


def run(packet_path, test_path, receipt_path, timeout):
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
        test = scratch / 'review_checks.py'
        test.write_bytes(test_bytes)
        stdout_path, stderr_path = scratch / 'stdout', scratch / 'stderr'
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(work)}
        with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
            proc = subprocess.Popen([sys.executable, '-B', '-c', HARNESS, str(test)], cwd=work,
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
    receipt = {'format': 'local-challenge-result/1', 'status': status,
               'packet': str(packet_path), 'packet_sha256': sha(packet_raw),
               'test': str(test_path), 'test_sha256': sha(test_bytes),
               'exit_code': code, 'timed_out': timed_out,
               'elapsed_seconds': round(time.monotonic() - started, 4),
               'test_summary': summary, 'stdout': stdout, 'stderr': stderr,
               'independent_identity_authenticated': False,
               'limits': 'Observed local unittest results only. Host verifies reviewer independence and business coverage. Not a sandbox, publication or production acceptance.'}
    write_new(receipt_path, receipt)
    return receipt


def status(receipt_path):
    receipt = load_json(Path(receipt_path).read_text())
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
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
    args = vars(parser.parse_args())
    op = args.pop('op')
    if op == 'freeze':
        return freeze(args['workspace'], args['input'], args['output'], args['packet'])
    if op == 'run':
        return run(args['packet'], args['test'], args['receipt'], args['timeout'])
    if op == 'status':
        return status(args['receipt'])
    if op == 'check':
        return check_relations(args['workspace'], args['spec'], args['receipt'])
    return render(**args)


if __name__ == '__main__':
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result['status'] in ('FROZEN', 'CHALLENGE_PASS', 'RELATIONS_PASS', 'RENDERED') else 1)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'status': 'ERROR', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
