"""Decision-derived cases must expose faults hidden by happy-path hand tests."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import gate
from core.decisions import compile_decision


def atom(fact, op, value):
    return {'fact': fact, 'op': op, 'value': value}


def admission():
    return {'id': 'admission', 'sources': ['request.md:1'],
            'facts': {'enabled': [True, False], 'score': {'step': 1}},
            'rules': [{'when': {'all': [atom('enabled', '==', True), atom('score', '>=', 5)]}, 'then': True}],
            'default': False, 'observe': 'admit'}


class DecisionSelectionTests(unittest.TestCase):
    def test_independent_rejection_and_threshold_equality_are_selected(self):
        result = compile_decision(admission())
        cases = {(x['facts']['enabled'], x['facts']['score']): x['expected'] for x in result['cases']}
        self.assertEqual(cases[(True, 4)], False)
        self.assertEqual(cases[(True, 5)], True)
        self.assertTrue(any(not enabled and score >= 5 and not expected for (enabled, score), expected in cases.items()))
        self.assertLess(len(cases), result['product_size'])

    def test_many_conditions_use_independent_witnesses_instead_of_full_product(self):
        d = {'id': 'permit', 'facts': {str('f' + str(i)): [True, False] for i in range(6)},
             'rules': [{'when': {'all': [atom('f' + str(i), '==', True) for i in range(6)]}, 'then': True}],
             'default': False, 'observe': 'observe'}
        result = compile_decision(d)
        self.assertEqual(result['product_size'], 64)
        self.assertEqual(len(result['cases']), 7)
        self.assertEqual(sum(x['expected'] for x in result['cases']), 1)
        self.assertTrue(all(sum(not v for v in x['facts'].values()) <= 1 for x in result['cases']))

    def test_ordered_priority_chooses_overlap_and_lower_rule_alone(self):
        d = {'id': 'route', 'facts': {'urgent': [True, False], 'member': [True, False]},
             'rules': [{'when': atom('urgent', '==', True), 'then': 'urgent'},
                       {'when': atom('member', '==', True), 'then': 'member'}],
             'default': 'ordinary', 'observe': 'route'}
        cases = compile_decision(d)['cases']
        self.assertIn({'facts': {'urgent': True, 'member': True}, 'expected': 'urgent'}, cases)
        self.assertIn({'facts': {'urgent': False, 'member': True}, 'expected': 'member'}, cases)

    def test_numeric_less_equal_and_range_have_equality_and_adjacent_cases(self):
        d = admission()
        d['facts'].pop('enabled')
        d['rules'][0]['when'] = {'all': [atom('score', '>', 3), atom('score', '<=', 8)]}
        result = compile_decision(d)
        values = {r['facts']['score']: r['expected'] for r in result['cases']}
        self.assertEqual(values, {3: False, 4: True, 8: True, 9: False})

    def test_full_state_machine_replication_is_rejected_in_favor_of_scalar_decision(self):
        d = admission()
        d['rules'][0]['then'] = {'allowed': True, 'state': 'saved'}
        with self.assertRaisesRegex(ValueError, 'scalar decision'):
            compile_decision(d)

    def test_large_models_fail_instead_of_truncating(self):
        d = admission()
        d['facts'] = {'f' + str(i): [0, 1, 2] for i in range(8)}
        d['rules'][0]['when'] = {'all': [atom('f' + str(i), '==', 1) for i in range(8)]}
        with self.assertRaisesRegex(ValueError, 'split'):
            compile_decision(d)

    def test_unreachable_rules_unused_facts_and_missing_boundaries_require_reconciliation(self):
        d = admission()
        d['rules'].append(copy.deepcopy(d['rules'][0]))
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            compile_decision(d)
        d = admission()
        d['facts']['ignored'] = [True, False]
        with self.assertRaisesRegex(ValueError, 'participate'):
            compile_decision(d)
        d = admission()
        d['facts']['score'] = [0, 10]
        with self.assertRaisesRegex(ValueError, 'step'):
            compile_decision(d)


class AdapterPathTests(unittest.TestCase):
    def test_windows_and_posix_absolute_literals_are_recognized(self):
        paths = [r'C:\projects\candidate workspace\app.py',
                 'C:/projects/candidate workspace/app.py',
                 r'\\server\share\candidate\app.py',
                 r'\\?\C:\projects\candidate\app.py',
                 r'\\?\UNC\server\share\candidate\app.py',
                 '/projects/candidate workspace/app.py']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(gate._absolute_literals('APP = ' + repr(path)), [path])

    def test_relative_paths_and_ordinary_strings_are_not_absolute(self):
        for value in ('candidate.py', '../candidate.py', r'C:candidate.py',
                      'expected result', 'https://example.com/app.py'):
            with self.subTest(value=value):
                self.assertEqual(gate._absolute_literals('VALUE = ' + repr(value)), [])


class DecisionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.control = self.root / 'control'
        self.task = self.control / 'task.json'
        (self.work / 'request.md').write_text('Enabled accounts with score at least 5 may enter.\n')
        (self.work / 'candidate.py').write_text('def admit(enabled, score): return enabled and score >= 5\n')
        gate.start(self.work, ['request.md'], ['candidate.py'], self.task)
        self.adapter = self.control / 'adapter.py'
        self.adapter.write_text('import candidate\ndef admit(facts): return candidate.admit(**facts)\n')
        self.plan = self.control / 'plan.json'
        self.plan.write_text(json.dumps({'test_file': str(self.adapter), 'decisions': [admission()]}))

    def test_existing_real_checks_do_not_require_rewriting_product_as_decision_model(self):
        self.adapter.write_text('import candidate, unittest\nclass Happy(unittest.TestCase):\n def test_ok(self):\n  self.assertTrue(candidate.admit(True,5))\n  self.assertFalse(candidate.admit(False,5))\n  self.assertFalse(candidate.admit(True,4))\n')
        p = {'test_file': str(self.adapter), 'outcomes': [{'id': 'ok', 'sources': ['request.md:1'], 'expected': 'Enabled and threshold boundaries'}],
             'checks': [{'id': 'ok', 'test': 'Happy.test_ok', 'covers': ['ok']}]}
        self.plan.write_text(json.dumps(p))
        self.assertEqual(gate.seal(self.task, self.plan)['selection'], [])
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')

    def test_compact_source_checks_execute_without_duplicate_outcome_registration(self):
        self.adapter.write_text('import candidate, unittest\nclass Business(unittest.TestCase):\n def test_real(self):\n  self.assertTrue(candidate.admit(True,5))\n  self.assertFalse(candidate.admit(True,4))\n  self.assertFalse(candidate.admit(False,5))\n')
        self.plan.write_text(json.dumps({'test_file': str(self.adapter), 'checks': [
            {'id': 'source_chain', 'test': 'Business.test_real', 'sources': ['request.md:1']}]}))
        gate.seal(self.task, self.plan)
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.assertEqual(gate.check(self.task, True)['executed_checks'], [])
        (self.work / 'candidate.py').write_text('def admit(enabled, score): return score >= 5\n')
        self.assertEqual(gate.check(self.task, True)['status'], 'BLOCKED')

    def test_derived_missing_condition_catches_real_fault_then_repair_and_reuse(self):
        sealed = gate.seal(self.task, self.plan)
        self.assertEqual(sealed['selection'][0]['selected_cases'], 3)
        (self.work / 'candidate.py').write_text('def admit(enabled, score): return score >= 5\n')
        result = gate.check(self.task, True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failed_outcomes'], ['admission'])
        self.assertEqual(gate.check(self.task, True)['executed_checks'], [])
        (self.work / 'candidate.py').write_text('def admit(enabled, score): return enabled and score >= 5\n')
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.assertEqual(gate.check(self.task, True)['executed_checks'], [])

    def test_one_passing_case_cannot_stand_for_all_distinctions_of_same_outcome(self):
        gate.seal(self.task, self.plan)
        first = gate.check(self.task)
        task = gate.read(self.task)
        plan = gate.read(task['plan'])
        selected = [plan['checks'][0]['test']]
        packet = Path(first['packet'])
        gate.challenge.run(packet, task['sealed']['test']['path'], packet.parent / 'partial.json', 5, selected)
        result = gate.check(self.task)
        self.assertEqual(result['missing_outcomes'], [])
        self.assertTrue(result['missing_checks'])
        self.assertEqual(result['status'], 'BLOCKED')

    def test_wrong_priority_survives_individual_rules_but_not_generated_overlap(self):
        d = {'id': 'route', 'sources': ['request.md:1'], 'facts': {'urgent': [True, False], 'member': [True, False]},
             'rules': [{'when': atom('urgent', '==', True), 'then': 'urgent'},
                       {'when': atom('member', '==', True), 'then': 'member'}],
             'default': 'ordinary', 'observe': 'route'}
        self.adapter.write_text('import candidate\ndef route(f): return candidate.route(**f)\n')
        self.plan.write_text(json.dumps({'test_file': str(self.adapter), 'decisions': [d]}))
        gate.seal(self.task, self.plan)
        (self.work / 'candidate.py').write_text('def route(urgent, member): return "member" if member else "urgent" if urgent else "ordinary"\n')
        self.assertEqual(gate.check(self.task, True)['status'], 'BLOCKED')

    def test_changed_adapter_or_generated_checks_revoke_release(self):
        gate.seal(self.task, self.plan)
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.adapter.write_text(self.adapter.read_text() + '\n# changed source interpretation\n')
        self.assertEqual(gate.check(self.task)['next_action'], 'RECONCILE_CHANGED_ACCEPTANCE_WITH_SOURCE')
        gate.seal(self.task, self.plan, 'Original policy unchanged; clarify adapter only.')
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        compiled = gate.read(self.task)['sealed']['test']['path']
        Path(compiled).write_text('import unittest\nclass Happy(unittest.TestCase):\n def test_pass(self): pass\n')
        self.assertEqual(gate.check(self.task)['status'], 'BLOCKED')

    def test_distinct_source_statements_cannot_hide_priority_inside_same_paragraph(self):
        from core.coverage import source_brief
        (self.work / 'request.md').write_text('允许符合条件者分配；容量最小者优先。同容量按ID排序。命令为 `a;b`。\n')
        brief = source_brief(self.work, ['request.md'], split_items=True, split_statements=True)
        self.assertEqual(len(brief['units']), 4)
        self.assertIn('同容量按ID排序', brief['units'][2]['text'])
        self.assertIn('`a;b`', brief['units'][3]['text'])
        plan = {'test_file': str(self.adapter), 'decisions': [dict(admission(), sources=[brief['units'][0]['id']])]}
        from core.decisions import compile_plan
        normalized, _, _ = compile_plan(brief, plan, self.adapter.read_text())
        self.assertEqual(gate.validate_plan(brief, normalized), {x['id'] for x in brief['units'][1:]})

    def test_literal_original_workspace_is_rejected_before_freezing_false_acceptance(self):
        paths = [str(self.work), str(self.work / 'candidate.py'),
                 self.work.as_posix(), (self.work / 'candidate.py').as_posix()]
        if sys.platform == 'win32':
            paths += [str(self.work).swapcase(), str(self.work / 'candidate.py').swapcase()]
            paths += ['\\\\?\\' + str(self.work), '\\\\?\\' + str(self.work / 'candidate.py')]
        for index, path in enumerate(dict.fromkeys(paths)):
            with self.subTest(path=path):
                task = self.control / ('literal-' + str(index)) / 'task.json'
                gate.start(self.work, ['request.md'], ['candidate.py'], task)
                self.adapter.write_text('from pathlib import Path\nAPP=Path(' + repr(path) + ')\ndef admit(facts): return True\n')
                with self.assertRaisesRegex(ValueError, 'frozen candidate copy'):
                    gate.seal(task, self.plan)
                self.assertEqual(gate.read(task)['state'], 'NEEDS_SOURCE_CHECKS')

    def test_unrelated_absolute_path_does_not_block_candidate_acceptance(self):
        self.adapter.write_text('import candidate\nREFERENCE = ' + repr(str(self.root / 'reference')) +
                                '\ndef admit(facts): return candidate.admit(**facts)\n')
        gate.seal(self.task, self.plan)
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')

    def test_indirect_original_path_cannot_observe_correct_original_instead_of_faulty_copy(self):
        # Split the literal so static checking is insufficient; runtime binding
        # must still reject opening the unfrozen original.
        bases = [str(self.work.parent), self.work.parent.as_posix()]
        if sys.platform == 'win32':
            bases.append(str(self.work.parent).swapcase())
        for index, base in enumerate(dict.fromkeys(bases)):
            with self.subTest(base=base):
                task = self.control / ('indirect-' + str(index)) / 'task.json'
                gate.start(self.work, ['request.md'], ['candidate.py'], task)
                self.adapter.write_text('from pathlib import Path\nBASE=Path(' + repr(base) + ')\ndef admit(facts):\n text=(BASE/"work"/"candidate.py").read_text()\n return facts["enabled"] and facts["score"] >= 5\n')
                gate.seal(task, self.plan)
                result = gate.check(task, True)
                self.assertEqual(result['status'], 'BLOCKED')
                receipt = gate.read(next(Path(result['packet']).parent.glob('*-replay.json')))
                self.assertGreater(receipt['test_summary']['errors'], 0)
                self.assertIn('frozen candidate copy', receipt['stderr']['text'])

    def test_json_boolean_is_not_accepted_as_integer_result(self):
        gate.seal(self.task, self.plan)
        (self.work / 'candidate.py').write_text('def admit(enabled, score): return int(enabled and score >= 5)\n')
        self.assertEqual(gate.check(self.task, True)['status'], 'BLOCKED')


if __name__ == '__main__': unittest.main()
