"""Compile source-derived decisions into a small discriminating executable suite.

The host writes policy and real-action adapters; this module chooses cases without
reading product code. No model, mutation campaign, role chain or scheduler.
"""
from collections import defaultdict
from itertools import combinations, product
import json
import math
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def atoms(expr):
    require(isinstance(expr, dict), 'condition must be an expression object')
    if set(expr) in ({'all'}, {'any'}):
        children = expr.get('all', expr.get('any'))
        require(isinstance(children, list) and children, 'all/any needs conditions')
        return [a for child in children for a in atoms(child)]
    if set(expr) == {'not'}:
        return atoms(expr['not'])
    require(set(expr) == {'fact', 'op', 'value'} and isinstance(expr['fact'], str),
            'atom needs fact, op, value')
    require(expr['op'] in ('==', '!=', '>', '>=', '<', '<=', 'in', 'not in'), 'unsupported comparison')
    if expr['op'] in ('in', 'not in'):
        require(isinstance(expr['value'], list) and expr['value'], 'membership needs values')
    return [expr]


def evaluate(expr, facts):
    if 'all' in expr:
        return all(evaluate(x, facts) for x in expr['all'])
    if 'any' in expr:
        return any(evaluate(x, facts) for x in expr['any'])
    if 'not' in expr:
        return not evaluate(expr['not'], facts)
    left, right, op = facts[expr['fact']], expr['value'], expr['op']
    if op == '==': return type(left) is type(right) and left == right
    if op == '!=': return not (type(left) is type(right) and left == right)
    if op == 'in': return any(type(left) is type(x) and left == x for x in right)
    if op == 'not in': return not any(type(left) is type(x) and left == x for x in right)
    return {'>': lambda: left > right, '>=': lambda: left >= right,
            '<': lambda: left < right, '<=': lambda: left <= right}[op]()


def compile_decision(decision):
    """Find branch, independent-fact boundary and overlapping-priority witnesses.

    Selection is a deterministic bounded set-cover heuristic, not a proof of a
    globally minimal suite. No check is dropped because a resource cap is met.
    """
    require(isinstance(decision, dict) and re.fullmatch(r'[A-Za-z_]\w*', decision.get('id', '')),
            'decision needs an identifier')
    ident = decision['id']
    facts, rules = decision.get('facts'), decision.get('rules')
    require(isinstance(facts, dict) and facts and len(facts) <= 12, 'decision needs 1..12 facts')
    require(isinstance(rules, list) and 0 < len(rules) <= 32 and 'default' in decision,
            'ordered rules and explicit default required')
    require(re.fullmatch(r'[A-Za-z_]\w*', decision.get('observe', '')), 'observe must name an adapter function')
    all_atoms = []
    for rule in rules:
        require(set(rule) == {'when', 'then'}, 'rule needs when and then')
        all_atoms.extend(atoms(rule['when']))
        require(not isinstance(rule['then'], (dict, list)), 'model a scalar decision only; keep full state/readback in existing journey checks')
        key(rule['then'])
    require(not isinstance(decision['default'], (dict, list)), 'model a scalar decision only; keep full state/readback in existing journey checks')
    key(decision['default'])
    require(set(facts) == {a['fact'] for a in all_atoms}, 'every declared fact must participate in policy')
    names, domains = list(facts), []
    for name in names:
        require(re.fullmatch(r'[A-Za-z_]\w*', name), 'invalid fact identifier')
        domain = facts[name]
        comparisons = [a for a in all_atoms if a['fact'] == name]
        if isinstance(domain, dict):
            step = domain.get('step')
            require(set(domain) == {'step'} and type(step) is int and step > 0,
                    'numeric fact needs a positive integer step; use smallest business unit')
            bounds = [a['value'] for a in comparisons]
            require(all(type(v) is int for v in bounds), 'numeric comparisons need integer boundaries')
            require(all(a['op'] not in ('in', 'not in') for a in comparisons), 'use explicit enum for membership')
            domain = sorted({v + offset * step for v in bounds for offset in (-1, 0, 1)})
        else:
            require(isinstance(domain, list) and 1 < len(domain) <= 32, 'enum needs 2..32 reachable values')
            require(len({key(v) for v in domain}) == len(domain), 'duplicate fact values')
            require(not any(a['op'] in ('>', '>=', '<', '<=') for a in comparisons),
                    'numeric inequalities require step so boundary cases cannot be omitted')
            values = [v for a in comparisons for v in (a['value'] if a['op'] in ('in', 'not in') else [a['value']])]
            require(all(key(v) in {key(x) for x in domain} for v in values), 'policy literal absent from enum')
        domains.append(domain)
    size = math.prod(map(len, domains))
    require(size <= 4096, 'decision product exceeds 4096: split independent business decisions, never truncate coverage')
    rows = []
    for values in product(*domains):
        inputs = dict(zip(names, values))
        matches = [i for i, rule in enumerate(rules) if evaluate(rule['when'], inputs)]
        winner = matches[0] if matches else len(rules)
        expected = rules[winner]['then'] if matches else decision['default']
        rows.append({'facts': inputs, 'expected': expected, 'winner': winner, 'matches': matches})
    # Every reachable rule and default outcome; an impossible rule is a model
    # error requiring source reconciliation, never a silently accepted test gap.
    reachable = {r['winner'] for r in rows}
    require(set(range(len(rules))) <= reachable, 'shadowed/unreachable policy rule: reconcile with source')
    obligations = {}
    for branch in sorted(reachable):
        obligations['branch:' + str(branch)] = [frozenset([i]) for i, r in enumerate(rows) if r['winner'] == branch]
    # Each independently influential fact/adjacent boundary must change the real
    # decision in a context where other facts are held fixed. A negative test
    # with two invalid facts cannot masquerade as independent condition evidence.
    for name, domain in zip(names, domains):
        groups = defaultdict(list)
        for i, row in enumerate(rows):
            groups[key({k: v for k, v in row['facts'].items() if k != name})].append(i)
        influential = False
        pairs = list(zip(domain, domain[1:])) if isinstance(facts[name], dict) else list(combinations(domain, 2))
        for low, high in pairs:
            witnesses = []
            for group in groups.values():
                candidates = {key(rows[i]['facts'][name]): i for i in group}
                a, b = candidates[key(low)], candidates[key(high)]
                if key(rows[a]['expected']) != key(rows[b]['expected']):
                    witnesses.append(frozenset([a, b]))
            if witnesses:
                influential = True
                obligations['contrast:' + name + ':' + key([low, high])] = witnesses
        require(influential, 'fact cannot affect any result: reconcile redundant/mis-modelled fact ' + name)
    # Priority only matters where two rules can apply simultaneously and disagree.
    for a, b in combinations(range(len(rules)), 2):
        if key(rules[a]['then']) == key(rules[b]['then']):
            continue
        choices = [frozenset([i]) for i, r in enumerate(rows) if a in r['matches'] and b in r['matches'] and r['winner'] == a]
        if choices:
            obligations['priority:' + str(a) + ':' + str(b)] = choices
    selected = set()
    pending = dict(obligations)
    while pending:
        options = set(p for choices in pending.values() for p in choices)
        def score(option):
            combined = selected | option
            gain = sum(any(p <= combined for p in choices) for choices in pending.values())
            cost = len(option - selected)
            return (-gain / max(cost, 1), cost, tuple(sorted(option)))
        best = min(options, key=score)
        selected.update(best)
        pending = {k: v for k, v in pending.items() if not any(p <= selected for p in v)}
    selected = sorted(selected)
    return {'id': ident, 'observe': decision['observe'], 'product_size': size,
            'obligations': sorted(obligations),
            'cases': [{'facts': rows[i]['facts'], 'expected': rows[i]['expected']} for i in selected]}


def compile_plan(brief, plan, adapter_text):
    """Augment existing hand-written journey tests; avoid a parallel evidence layer."""
    decisions = plan.get('decisions')
    require(isinstance(decisions, list) and 0 < len(decisions) <= 16, 'need 1..16 source decisions')
    source_ids = {u['id'] for u in brief['units']}
    outcomes = list(plan.get('outcomes', []))
    checks = list(plan.get('checks', []))
    summaries, generated = [], []
    seen = {o['id'] for o in outcomes}
    for decision in decisions:
        require(isinstance(decision.get('sources'), list) and decision['sources'] and set(decision['sources']) <= source_ids,
                'decision.sources must name original brief units')
        compiled = compile_decision(decision)
        ident = compiled['id']
        require(ident not in seen, 'duplicate decision/outcome identifier')
        seen.add(ident)
        outcomes.append({'id': ident, 'sources': decision['sources'], 'expected': 'Source decision ' + ident})
        for n, case in enumerate(compiled['cases']):
            name = 'test_' + ident + '_' + str(n)
            test = 'GeneratedDecisions.' + name
            checks.append({'id': ident + '_' + str(n), 'test': test, 'covers': [ident]})
            generated.append(' def ' + name + '(self):\n  self.assertEqual(_decision_json.dumps(' + compiled['observe'] + '(' + repr(case['facts']) + '), sort_keys=True, allow_nan=False), ' + repr(json.dumps(case['expected'], sort_keys=True, allow_nan=False)) + ')\n')
        summaries.append(compiled)
    text = adapter_text + '\n\nimport unittest as _decision_unittest\nimport json as _decision_json\nclass GeneratedDecisions(_decision_unittest.TestCase):\n' + ''.join(generated)
    require(len(text.encode()) <= 65536, 'generated checks exceed runner limit: select a business slice')
    result = {**plan, 'outcomes': outcomes, 'checks': checks}
    result.pop('decisions')
    return result, text, summaries
