"""Source-accounted check selection. No domain keywords, model or scheduler.

The host still judges whether source interpretation and assertions are adequate.
This module verifies declared coverage and fresh, attributable execution only.
"""
from fractions import Fraction
import hashlib
from pathlib import Path
import re

from .local_checks import artifact, file_bytes, load_json


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def source_brief(workspace, inputs, split_items=False):
    root = Path(workspace).resolve(strict=True)
    require(inputs and len(inputs) == len(set(inputs)), 'distinct source documents required')
    units = []
    for name in inputs:
        text = file_bytes(root, name).decode('utf-8-sig')
        require(len(text.encode()) <= 65536, 'source document too large; select a business slice')
        # Paragraphs remain intact; a paragraph may contain several obligations.
        # Headings carry context rather than forcing a separate test obligation.
        chunk, first = [], 0
        for line_no, line in enumerate(text.splitlines() + [''], 1):
            item_boundary = split_items and re.match(r'^\s*(?:\d+[.)]|[-*+])\s+', line)
            if item_boundary and chunk:
                units.append({'id': name + ':' + str(first), 'path': name,
                              'line': first, 'text': '\n'.join(chunk)})
                chunk = []
            if not line.strip() or re.match(r'^\s*#{1,6}\s', line):
                if chunk:
                    units.append({'id': name + ':' + str(first), 'path': name,
                                  'line': first, 'text': '\n'.join(chunk)})
                    chunk = []
            else:
                if not chunk:
                    first = line_no
                chunk.append(line)
    require(0 < len(units) <= 200, 'need 1..200 source paragraphs; select a business slice')
    return {'format': 'local-coverage-brief/1', 'workspace': str(root),
            'inputs': [artifact(root, name) for name in inputs], 'units': units,
            'limits': 'Every paragraph must be accounted for, but paragraphs are not a semantic requirements parser. The host must inspect all material obligations within them.'}


def validate_plan(brief, plan):
    units = {u['id'] for u in brief['units']}
    outcomes, checks, context = plan.get('outcomes'), plan.get('checks'), plan.get('context', [])
    require(isinstance(outcomes, list) and 0 < len(outcomes) <= 128, 'plan.outcomes needs 1..128 outcomes')
    require(isinstance(checks, list) and len(checks) <= 128, 'plan.checks must be a list of at most 128 checks')
    require(isinstance(context, list), 'plan.context must be a list')
    ids, mapped = set(), set()
    for item in outcomes:
        require(isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip() and item['id'] not in ids, 'unique outcome IDs required')
        ids.add(item['id'])
        require(isinstance(item.get('expected'), str) and item['expected'].strip(), 'observable outcome.expected required')
        require(type(item.get('priority', 2)) is int and 1 <= item.get('priority', 2) <= 3, 'priority must be 1..3; every outcome remains required')
        source_ids = item.get('sources')
        require(isinstance(source_ids, list) and source_ids and all(u in units for u in source_ids), 'outcome.sources must name real brief units')
        mapped.update(source_ids)
    for item in context:
        require(isinstance(item, dict) and item.get('source') in units and isinstance(item.get('reason'), str) and item['reason'].strip(), 'context needs real source and specific exclusion reason')
        mapped.add(item['source'])
    check_ids, tests = set(), set()
    for item in checks:
        require(isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip() and item['id'] not in check_ids, 'unique check IDs required')
        check_ids.add(item['id'])
        require(isinstance(item.get('test'), str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', item['test']) and item['test'] not in tests, 'unique Class.test_method names required')
        tests.add(item['test'])
        require(isinstance(item.get('covers'), list) and item['covers'] and set(item['covers']) <= ids, 'check.covers must name real outcomes')
        require(type(item.get('cost', 1)) is int and 1 <= item.get('cost', 1) <= 100, 'check.cost must be 1..100 relative local effort units')
        require(type(item.get('required', True)) is bool, 'check.required must be boolean')
        if not item.get('required', True):
            require(isinstance(item.get('why_optional'), str) and item['why_optional'].strip(), 'optional check needs a specific reason; distinct required scenarios are not substitutes')
    require(isinstance(plan.get('test_file'), str) and plan['test_file'], 'plan.test_file required')
    return units - mapped


def choose_checks(outcomes, checks, covered, budget, completed_checks=()):
    """Greedy marginal coverage/effort, reusing a test for multiple outcomes.

    This is a bounded heuristic, not a proof of minimum cost or token savings.
    Budget limits selection, never downgrades an unmet outcome into acceptance.
    """
    weights = {o['id']: (1, 10, 100)[o.get('priority', 2) - 1] for o in outcomes}
    pending = set(weights) - set(covered)
    pending_checks = {c['id'] for c in checks if c.get('required', True)} - set(completed_checks)
    selected, spent = [], 0
    remaining = list(checks)
    while pending or pending_checks:
        choices = [c for c in remaining if (set(c['covers']) & pending or c['id'] in pending_checks) and spent + c.get('cost', 1) <= budget]
        if not choices:
            break
        def gain(c):
            benefit = sum(weights[x] for x in set(c['covers']) & pending)
            if c['id'] in pending_checks:
                benefit += max(weights[x] for x in c['covers'])
            return Fraction(benefit, c.get('cost', 1))
        best = min(choices, key=lambda c: (-gain(c), c.get('cost', 1), c['id']))
        selected.append(best)
        spent += best.get('cost', 1)
        pending -= set(best['covers'])
        pending_checks.discard(best['id'])
        remaining.remove(best)
    return selected, spent


def inspect_evidence(plan, packet_path, evidence_paths, status_fn):
    expected_packet = digest(packet_path)
    expected_test = digest(plan['test_file'])
    by_test = {c['test']: c for c in plan['checks']}
    passed, failed, passed_checks, failed_checks, unmapped_failures, observations, dependencies = set(), set(), set(), set(), set(), [], []
    for path in dict.fromkeys(str(Path(p).resolve()) for p in evidence_paths):
        dep = digest(path)
        dependencies.append(dep)
        receipt = load_json(Path(path).read_text())
        reason, valid = '', False
        if receipt.get('format') != 'local-challenge-result/1':
            reason = 'not an attributable challenge execution; relation/self-written PASS cannot substitute'
        elif receipt.get('packet_sha256') != expected_packet['sha256'] or receipt.get('packet') != expected_packet['path']:
            reason = 'different candidate packet'
        elif receipt.get('test_sha256') != expected_test['sha256'] or receipt.get('test') != expected_test['path']:
            reason = 'different check script'
        else:
            current = status_fn(path)['status']
            cases = receipt.get('test_cases', [])
            # A current crashed/unknown execution is counterevidence even when
            # a prior run passed. Aggregate failure cannot vanish merely because
            # teardown/import/transport did not identify a failing test method.
            if current == 'UNKNOWN' or (current == 'FAIL' and not any(c.get('status') == 'FAIL' for c in cases)):
                unmapped_failures.add('EXECUTION_' + current + ':' + str(path))
            valid = current in ('CHALLENGE_PASS', 'FAIL') and bool(cases)
            if not valid:
                reason = current + ': no attributable current case evidence'
        if valid:
            for case in receipt['test_cases']:
                check = by_test.get(case['id'])
                if not check:
                    if case['status'] == 'FAIL':
                        unmapped_failures.add(case['id'])
                    continue
                if case['status'] == 'FAIL':
                    failed.update(check['covers'])
                    failed_checks.add(check['id'])
                elif case['status'] == 'PASS':
                    passed.update(check['covers'])
                    passed_checks.add(check['id'])
        observations.append({'receipt': path, 'usable': valid, 'reason': reason})
    # A fresh failure cannot be erased by later importing a PASS for the same bytes.
    return passed - failed, failed, passed_checks - failed_checks, unmapped_failures, observations, dependencies


def discover_evidence(directories):
    """Reuse local receipts, including failures the caller forgot to list.

    Only immediate, caller-owned control directories are scanned, never a repo,
    runtime queue or unrelated chat. Other candidate/test versions are filtered
    later. Moved receipts still need an explicit --evidence reference.
    """
    paths = sorted({p.resolve() for d in directories for p in Path(d).glob('*.json')})
    require(len(paths) <= 256, 'control directory too large; isolate the current review')
    receipts = []
    for path in paths:
        if path.stat().st_size > 262144:
            continue
        try:
            value = load_json(path.read_text())
            if isinstance(value, dict) and value.get('format') == 'local-challenge-result/1':
                receipts.append(str(path))
        except (OSError, ValueError):
            continue
    return receipts


def coverage_report(brief_path, plan_path, packet_path, evidence_paths, budget, status_fn, current_fn):
    require(type(budget) is int and 0 <= budget <= 12800, 'budget must be 0..12800 relative check effort units')
    brief_path, plan_path, packet_path = map(lambda p: Path(p).resolve(strict=True), (brief_path, plan_path, packet_path))
    brief, plan, packet = [load_json(p.read_text()) for p in (brief_path, plan_path, packet_path)]
    require(brief.get('format') == 'local-coverage-brief/1', 'need a source brief')
    require(packet.get('format') == 'local-challenge/1', 'need a frozen candidate packet')
    require(brief['workspace'] == packet['workspace'], 'brief and packet workspace differ')
    require(all(x in packet['inputs'] for x in brief['inputs']), 'candidate packet must bind every source document')
    require(current_fn({'workspace': brief['workspace'], 'inputs': brief['inputs'], 'outputs': []}), 'STALE_SOURCE: rebuild brief from current source documents')
    require(current_fn(packet), 'STALE_CANDIDATE: freeze current candidate')
    uncovered_sources = validate_plan(brief, plan)
    evidence_paths = list(evidence_paths) + discover_evidence([plan_path.parent])
    passed, failed, passed_checks, unmapped_failures, observations, dependencies = inspect_evidence(plan, packet_path, evidence_paths, status_fn)
    all_ids = {o['id'] for o in plan['outcomes']}
    missing = all_ids - passed
    missing_checks = {c['id'] for c in plan['checks'] if c.get('required', True)} - passed_checks
    selected, spent = choose_checks(plan['outcomes'], plan['checks'], passed, budget, passed_checks)
    unknown_checks = missing - {o for c in plan['checks'] for o in c['covers']}
    failure_sources = {u for o in plan['outcomes'] if o['id'] in failed for u in o['sources']}
    related = sorted(o['id'] for o in plan['outcomes'] if set(o['sources']) & failure_sources)
    if failed or unmapped_failures:
        action = 'REPAIR_AND_RECHECK_RELATED_OUTCOMES'
    elif uncovered_sources:
        action = 'REVIEW_UNACCOUNTED_SOURCE'
    elif (missing or missing_checks) and selected:
        action = 'RUN_SELECTED_CHECKS'
    elif unknown_checks:
        action = 'ADD_CHECK_FOR_MISSING_OUTCOME'
    elif missing or missing_checks:
        action = 'BUDGET_OR_ENVIRONMENT_INSUFFICIENT'
    else:
        action = 'CONFIRM_SEMANTIC_SUFFICIENCY'
    if failed or unmapped_failures or uncovered_sources:
        selected, spent = [], 0
    sufficient = not (missing or missing_checks or uncovered_sources or failed or unmapped_failures)
    dependencies += [digest(p) for p in (brief_path, plan_path, packet_path, Path(plan['test_file']))]
    return {'format': 'local-coverage-result/1', 'status': 'DECLARED_SCOPE_COVERED' if sufficient else 'INSUFFICIENT',
            'workspace': brief['workspace'], 'packet': str(packet_path), 'evidence': [o['receipt'] for o in observations],
            'control_directories': [str(plan_path.parent)],
            'dependencies': dependencies, 'covered_outcomes': sorted(passed), 'missing_outcomes': sorted(missing),
            'failed_outcomes': sorted(failed), 'related_outcomes_to_review': related,
            'completed_checks': sorted(passed_checks), 'missing_required_checks': sorted(missing_checks),
            'unmapped_failed_tests': sorted(unmapped_failures),
            'unaccounted_source_units': sorted(uncovered_sources), 'outcomes_without_checks': sorted(unknown_checks),
            'selected_checks': selected, 'selected_effort_units': spent, 'budget_effort_units': budget,
            'evidence_observations': observations, 'next_action': action,
            'limits': 'Declared coverage only; source interpretation, assertion quality, independence, native UI/external environments and overall business acceptance require host judgment. Relative effort is not model tokens or quota.'}


def fresh_report(report, current_fn, status_fn):
    try:
        if not all(digest(d['path']) == d for d in report['dependencies']):
            return False
        if set(discover_evidence(report.get('control_directories', []))) - set(report['evidence']):
            # New counterevidence needs reconciliation, even if the earlier
            # bytes still match. A prior covered report is not a timeless pass.
            return False
        packet = load_json(Path(report['packet']).read_text())
        return current_fn(packet) and all(status_fn(p)['status'] not in ('STALE',) for p in report['evidence'])
    except (OSError, ValueError, KeyError, TypeError):
        return False
