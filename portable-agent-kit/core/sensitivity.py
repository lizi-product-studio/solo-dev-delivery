"""Check whether chosen business observations distinguish plausible wrong behavior.

No industry rules, model calls, scheduler or persistent project state. Only
trusted local test code and declared Python output replacements in temp copies.
"""
from pathlib import Path
import hashlib
import json


def digest(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def recipe(workspace, inputs, outputs, test):
    return {'workspace': str(Path(workspace).resolve(strict=True)),
            'inputs': sorted(inputs), 'outputs': sorted(outputs),
            'test': str(Path(test).resolve(strict=True))}


def outside(path, root):
    path = Path(path).resolve()
    if path == root or root in path.parents:
        raise ValueError('sensitivity controls must be outside candidate workspace')
    return path


def load(path):
    return json.loads(Path(path).read_text())


def records(directory):
    paths = sorted(Path(directory).glob('*.json'))
    if len(paths) > 256:
        raise ValueError('isolate the current control directory')
    for path in paths:
        if path.stat().st_size > 262144:
            continue
        try:
            value = load(path)
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get('format') == 'local-sensitivity-result/1':
            yield path, value


def selected_probes(workspace, inputs, outputs, test, report):
    """Omitting a flag must not silently drop an already selected critical risk."""
    request = recipe(workspace, inputs, outputs, test)
    sidecar = Path(test).with_suffix('.probes.json')
    chosen = {v['spec']['path'] for _, v in records(Path(report).parent)
              if v.get('request') == request}
    if sidecar.exists():
        chosen.add(str(sidecar.resolve()))
    if len(chosen) > 1:
        raise ValueError('multiple selected probe specs; explicitly reconcile the current basis')
    if chosen:
        path = Path(next(iter(chosen)))
        if not path.is_file():
            raise ValueError('selected probe spec missing; preserve and reconcile its findings')
        return path
    return None


def fresh(record, status_fn):
    try:
        return (all(digest(d['path']) == d for d in record['dependencies'])
                and all(status_fn(p)['status'] != 'STALE' for p in record['executions']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def short(record, path, reused):
    return {'status': record['status'], 'receipt': str(path), 'reused': reused,
            'executed_tests': 0 if reused else record['executed_tests'],
            'probes': [{'id': p['id'], 'observation': p['observation']} for p in record['probes']],
            'next_action': record['next_action'], 'limits': record['limits']}


def verify_sensitive(workspace, inputs, outputs, test, report, timeout, spec_path,
                     verify_fn, run_fn, status_fn, write_fn):
    request = recipe(workspace, inputs, outputs, test)
    root = Path(request['workspace'])
    report = outside(report, root)
    spec_path = outside(spec_path, root).resolve(strict=True)
    spec = digest(spec_path)
    items = load(spec_path).get('probes')
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError('choose 1..8 concrete critical-risk probes, or use native observations')
    ids = set()
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ('id', 'basis', 'risk', 'test')):
            raise ValueError('probe needs id, original basis, wrong behavior and test method')
        if item['id'] in ids:
            raise ValueError('probe ids must be unique')
        ids.add(item['id'])
        patches = item.get('patches')
        if not isinstance(patches, list) or not 1 <= len(patches) <= 8:
            raise ValueError('probe needs a small declared counterfactual replacement')
        if any(not isinstance(p, dict) or any(not isinstance(p.get(k), str) for k in ('path','before','after'))
               for p in patches):
            raise ValueError('patch needs string path/before/after')
    matches = [(p, v) for p, v in records(report.parent)
               if v.get('request') == request and v.get('spec') == spec and fresh(v, status_fn)]
    if matches:
        p, value = sorted(matches, key=lambda pair: (pair[1]['status'] == 'SENSITIVITY_PASS', str(pair[0])))[0]
        return short(value, p, True)
    if report.exists():
        raise ValueError('sensitivity receipt exists; preserve it and choose a new report')
    base_path = report.with_name(report.stem + '-suite.json')
    base = verify_fn(workspace, inputs, outputs, test, base_path, timeout)
    actual = Path(base['receipt'])
    baseline = load(actual)
    dependencies = [spec, digest(actual)]
    executions = [str(actual)]
    observations = []
    executed = base['executed_tests']
    verdict = 'INSUFFICIENT'
    action = 'REPAIR_CANDIDATE_OR_ENVIRONMENT'
    if base['status'] == 'CHALLENGE_PASS':
        passed = {c['id'] for c in baseline['test_cases'] if c['status'] == 'PASS'}
        for index, item in enumerate(items):
            observation = {'id': item['id'], 'basis': item['basis'], 'risk': item['risk'],
                           'test': item['test'], 'observation': 'MISSING_BASE_OBSERVATION'}
            if item['test'] in passed:
                probe_path = report.with_name(report.stem + '-probe-' + str(index + 1) + '.json')
                try:
                    result = run_fn(baseline['packet'], test, probe_path, timeout,
                                    [item['test']], patches=item['patches'])
                    executions.append(str(probe_path))
                    dependencies.append(digest(probe_path))
                    summary = result.get('test_summary') or {}
                    executed += summary.get('tests', 0)
                    rejected = (result['status'] == 'FAIL' and summary.get('failures', 0) > 0
                                and not any(summary.get(k, 0) for k in
                                            ('errors','skipped','expected_failures','unexpected_successes')))
                    observation['observation'] = ('REJECTED_BY_ASSERTION' if rejected else
                        'SURVIVED' if result['status'] == 'CHALLENGE_PASS' else 'INVALID_OR_UNKNOWN')
                    observation['receipt'] = str(probe_path)
                except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
                    observation.update(observation='INVALID_PROBE', error=str(exc))
            observations.append(observation)
            if observation['observation'] != 'REJECTED_BY_ASSERTION':
                observations.extend({'id': p['id'], 'basis': p['basis'], 'risk': p['risk'],
                                     'test': p['test'], 'observation': 'DEFERRED_AFTER_GAP'}
                                    for p in items[index + 1:])
                break
        if all(p['observation'] == 'REJECTED_BY_ASSERTION' for p in observations):
            verdict, action = 'SENSITIVITY_PASS', 'HOST_CONFIRM_SOURCE_CHAIN_AND_STOP'
        else:
            action = 'ADD_DISTINGUISHING_OBSERVATION_OR_FIX_PROBE'
    record = {'format': 'local-sensitivity-result/1', 'status': verdict,
              'request': request, 'spec': spec, 'base_receipt': str(actual),
              'probes': observations, 'executed_tests': executed,
              'dependencies': dependencies, 'executions': executions, 'next_action': action,
              'limits': 'Only declared plausible faults and selected observations. Host must verify source interpretation, full causal chain, counterfactual validity and prose; not overall business acceptance.'}
    if not fresh(record, status_fn):
        record.update(status='STALE', next_action='RECONCILE_CHANGED_BASIS')
    write_fn(report, record)
    return short(record, report, False)
