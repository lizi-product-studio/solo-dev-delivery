"""Goal entry, authority and actual local artifacts; model decisions are doubles.

The subprocess double is explicitly synthetic, with no network/model credentials.
The production entry, file tools, contract compilation and trusted review execute.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portable-agent-kit'))
sys.path.insert(1, str(ROOT / 'portable-agent-kit/scripts'))
from agent_runtime.intake import IntakeError, build_context, compile_contract, planner_request, validate_plan
from agent_task import project_storage

CLI = ROOT / 'portable-agent-kit/scripts/agent_task.py'

GOAL = 'Read numbers.json and write the exact sum to report.json; preserve all input values.'
PLAN = {'inputs': ['numbers.json'], 'outputs': ['report.json'],
        'steps': ['Read the original values', 'Write the computed sum', 'Request trusted readback'], 'unknowns': []}
REVIEW = '''import json,unittest
from pathlib import Path
class IndependentSum(unittest.TestCase):
    def test_recompute_from_original_input(self):
        source=json.loads(Path('numbers.json').read_text(encoding='utf-8'))
        output=json.loads(Path('report.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(source['values']),output['total'])
'''


class GoalIntakeTests(unittest.TestCase):
    def context(self, root, allowed=None):
        workspace = root / 'workspace'
        workspace.mkdir()
        (workspace / 'numbers.json').write_text(json.dumps({'values': [20, 22]}), encoding='utf-8')
        return build_context(GOAL, workspace, allowed or ['report.json'], 'human-request:local-test', max_steps=6)

    def test_plan_is_bounded_by_host_authority_and_cannot_supply_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            context = self.context(Path(temp))
            for changes in [{'policy': {'external_actions': True}}, {'budget': {'max_steps': 999}},
                            {'outputs': ['other.json']}, {'inputs': ['missing.json']},
                            {'inputs': ['numbers.json'], 'outputs': ['numbers.json']},
                            {'outputs': ['../outside.json']}, {'outputs': ['C:/outside.json']},
                            {'outputs': ['report.json:secret']}, {'outputs': ['NUL.txt']}]:
                with self.subTest(changes=changes), self.assertRaises(IntakeError):
                    validate_plan({**PLAN, **changes}, context)
            self.assertEqual(PLAN, validate_plan(PLAN, context))

    def test_existing_editable_input_becomes_hash_bound_output_after_authority_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial_context = self.context(root)
            workspace = Path(initial_context['workspace'])
            source = workspace / 'pricing.py'
            source.write_text('def price():\n    return 1\n', encoding='utf-8')
            (workspace / 'TASK.md').write_text('Repair the pricing helper, preserving its public interface.', encoding='utf-8')
            context = build_context('Repair the pricing helper.', workspace, ['pricing.py'], 'host-authorized-edit', max_steps=6)
            proposal = {'inputs': ['TASK.md', './pricing.py'], 'outputs': ['pricing.py'],
                        'steps': ['Read the existing helper', 'Make the bounded repair', 'Verify behavior'], 'unknowns': []}
            plan = validate_plan(proposal, context)
            self.assertEqual(['TASK.md'], plan['inputs'])
            self.assertEqual(['pricing.py'], plan['outputs'])
            contract = compile_contract(context, proposal, task_id='edit-existing',
                review_sha256='a' * 64, planner_steps=1)
            self.assertEqual(['TASK.md'], contract['inputs'])
            self.assertEqual(['pricing.py'], contract['scope'])
            self.assertEqual([{'path': 'pricing.py', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}],
                             contract['intake']['initial_outputs'])
            self.assertEqual(plan, contract['intake']['plan'])
            self.assertTrue(contract['intake']['plan_is_advisory'])
            self.assertTrue(contract['policy']['require_input_reads'])
            self.assertEqual('a' * 64, contract['required_review_sha256'])

            readonly_context = build_context(context['goal'], workspace, ['report.json'], 'read-only-source', max_steps=6)
            with self.assertRaisesRegex(IntakeError, 'authorization'):
                validate_plan(proposal, readonly_context)
            if os.name == 'nt':
                self.assertEqual(['TASK.md'], validate_plan({**proposal, 'inputs': ['TASK.md', 'PRICING.PY']}, context)['inputs'])

    def test_duplicate_artifacts_within_one_list_still_fail_after_path_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            context = self.context(Path(temp), ['.'])
            variants = [{'inputs': ['numbers.json', './numbers.json']},
                        {'outputs': ['report.json', './report.json']}]
            if os.name == 'nt':
                variants.append({'inputs': ['numbers.json', 'NUMBERS.JSON']})
            for changes in variants:
                with self.subTest(changes=changes), self.assertRaisesRegex(IntakeError, 'duplicate'):
                    validate_plan({**PLAN, **changes}, context)

    def test_directory_authority_does_not_authorize_sibling_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            context = self.context(Path(temp), ['reports/'])
            plan = {**PLAN, 'outputs': ['reports/a.json']}
            self.assertEqual(['reports/a.json'], validate_plan(plan, context)['outputs'])
            with self.assertRaises(IntakeError):
                validate_plan({**PLAN, 'outputs': ['reports-other/a.json']}, context)

    def test_symlink_and_input_change_are_not_accepted_as_new_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = self.context(root, ['.'])
            workspace = Path(context['workspace'])
            (workspace / 'numbers.json').write_text('{"values":[100]}', encoding='utf-8')
            with self.assertRaisesRegex(IntakeError, 'changed'):
                validate_plan(PLAN, context)
            context = build_context(GOAL, workspace, ['.'], 'test', max_steps=6)
            link = workspace / 'linked.json'
            try:
                link.symlink_to(workspace / 'numbers.json')
            except OSError:
                return  # Windows without symlink privilege still ran input drift assertion.
            with self.assertRaisesRegex(IntakeError, 'symbolic'):
                validate_plan({**PLAN, 'inputs': ['linked.json']}, context)

    def test_generated_contract_keeps_full_goal_source_and_distinct_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            context = self.context(Path(temp))
            contract = compile_contract(context, PLAN, task_id='t', review_sha256='a' * 64, planner_steps=1)
            self.assertEqual(GOAL, contract['goal'])
            self.assertEqual('human-request:local-test', contract['authorization_ref'])
            self.assertEqual('a' * 64, contract['required_review_sha256'])
            self.assertEqual(5, contract['budget']['model_steps'])
            self.assertEqual(7, contract['budget']['max_steps'])
            self.assertEqual(6, contract['intake']['total_decision_budget'])
            self.assertEqual(hashlib.sha256(GOAL.encode()).hexdigest(), contract['intake']['goal_sha256'])
            self.assertEqual(PLAN, contract['intake']['plan'])
            self.assertTrue(contract['policy']['require_input_reads'])
            self.assertEqual(['submit_plan'], list(planner_request(context, 't')['tools']))
            for review_hash in (None, '', 'PASS'):
                with self.subTest(review_hash=review_hash), self.assertRaises(IntakeError):
                    compile_contract(context, PLAN, task_id='t', review_sha256=review_hash, planner_steps=1)

    def invoke(self, root, *, supplied_plan=True, review=True, wrong=False, unknowns=None,
               workspace=None, project_store=None, goal=GOAL, output='report.json',
               expected_success=None, forbidden_success=None):
        root.mkdir(parents=True, exist_ok=True)
        context = self.context(root) if workspace is None else build_context(
            goal, workspace, [output], 'human-request:local-test', max_steps=6)
        plan = {**PLAN, 'outputs': [output], 'unknowns': unknowns or []}
        (root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        reviewer = root / 'review.py'
        reviewer.write_text(REVIEW.replace("'report.json'", repr(output)), encoding='utf-8')
        fake = root / 'model.py'
        fake.write_text('''import json,sys
if hasattr(sys.stdin,'reconfigure'):sys.stdin.reconfigure(encoding='utf-8')
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
req=json.loads(sys.stdin.read().split('Request:\\n',1)[1])
assert req['goal']==GOAL
if list(req['tools'])!=['submit_plan'] and req['step']==1:
    previous=[row for row in req['memory'] if row['kind']=='success']
    if EXPECTED is not None: assert any(row['text']==EXPECTED for row in previous),'accepted prior task missing from scoped memory'
    if FORBIDDEN is not None: assert not any(row['text']==FORBIDDEN for row in previous),'different workspace history leaked'
    print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':json.dumps({'retrieved_successes':previous})}}))
if list(req['tools'])==['submit_plan']:
    decision={'action':'tool','name':'submit_plan','arguments':PLAN}
elif req['step']==1:
    decision={'action':'tool','name':'read_json','arguments':{'path':'numbers.json'}}
elif req['step']==2:
    total=sum(req['observations'][-1]['result']['data']['values'])
    decision={'action':'tool','name':'write_json','arguments':{'path':OUTPUT,'value':{'total':total-1 if WRONG else total}}}
else:
    decision={'action':'finish','answer':'artifact ready for independent readback'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(decision)}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':5}}))
'''.replace('GOAL', repr(goal)).replace('PLAN', repr(plan)).replace('WRONG', repr(wrong))
   .replace('OUTPUT', repr(output)).replace('EXPECTED', repr(expected_success))
   .replace('FORBIDDEN', repr(forbidden_success)), encoding='utf-8')
        argv = [sys.executable, str(CLI), '--goal', goal, '--workspace', context['workspace'],
                '--allow-write', output, '--authorization-ref', 'human-request:local-test',
                '--project-store', str(project_store if project_store is not None else root / 'project-store'),
                '--control-root', str(root / 'control'), '--max-steps', '6']
        if supplied_plan:
            argv += ['--plan', str(root / 'plan.json')]
        if review:
            argv += ['--review-script', str(reviewer)]
        proc = subprocess.run(argv, env={**os.environ, 'CODEX_CLI_PATH': str(fake)},
                              capture_output=True, encoding='utf-8', timeout=20)
        receipt = root / 'control/intake-receipt.json'
        self.assertTrue(receipt.exists(), proc.stderr)
        return proc, json.loads(receipt.read_text(encoding='utf-8'))

    def test_full_entry_runs_without_a_handwritten_contract_and_counts_planning(self):
        for supplied in (False, True):
            with self.subTest(supplied_plan=supplied), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                proc, report = self.invoke(root, supplied_plan=supplied)
                self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
                self.assertEqual('COMPLETED', report['status'])
                contract = json.loads((root / 'control/contract.json').read_text(encoding='utf-8'))
                self.assertEqual(0 if supplied else 1, report['planning']['decision_steps'])
                self.assertEqual(6 - report['planning']['decision_steps'], contract['budget']['model_steps'])
                self.assertEqual(42, json.loads((root / 'workspace/report.json').read_text())['total'])
                runtime = json.loads((root / 'control/receipt.json').read_text(encoding='utf-8'))
                self.assertEqual(report['task_id'], runtime['task_id'])
                self.assertEqual('COMPLETED', runtime['control_state']['status'])
                self.assertEqual(REVIEW, (root / 'control/trusted-review.py').read_text(encoding='utf-8'))

    def test_public_entry_reuses_accepted_memory_across_tasks_and_isolates_other_workspaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_base = root / 'project-state'
            first_goal = GOAL + ' This is the first accepted task.'
            first_root = root / 'first'
            first_proc, first = self.invoke(first_root, project_store=shared_base, goal=first_goal)
            self.assertEqual(0, first_proc.returncode, first_proc.stderr + first_proc.stdout)
            second_root = root / 'second'
            second_goal = 'Read numbers.json and write the exact sum to recheck.json, consulting earlier accepted task history as context.'
            second_proc, second = self.invoke(second_root, workspace=first_root / 'workspace',
                project_store=shared_base, goal=second_goal, output='recheck.json', expected_success=first_goal)
            self.assertEqual(0, second_proc.returncode, second_proc.stderr + second_proc.stdout)
            self.assertNotEqual(first['task_id'], second['task_id'])
            self.assertNotEqual(first['control_root'], second['control_root'])
            self.assertEqual(first['learning_databases'], second['learning_databases'])
            self.assertEqual(first['memory_scope'], second['memory_scope'])
            events_path = second_root / 'control/model-events' / second['task_id'] / 'step-001/stdout.jsonl'
            events = [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines()]
            evidence = [json.loads(event['item']['text']) for event in events
                        if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'reasoning']
            self.assertTrue(any(row['text'] == first_goal and row['task_id'] == first['task_id']
                                for packet in evidence for row in packet['retrieved_successes']))

            third_root = root / 'isolated'
            third_proc, third = self.invoke(third_root, project_store=shared_base,
                goal='Read numbers.json and write the exact sum to isolated.json in this separate workspace.',
                output='isolated.json', forbidden_success=first_goal)
            self.assertEqual(0, third_proc.returncode, third_proc.stderr + third_proc.stdout)
            self.assertNotEqual(first['memory_scope'], third['memory_scope'])
            for kind in ('memory', 'skills'):
                self.assertNotEqual(first['learning_databases'][kind], third['learning_databases'][kind])
                self.assertTrue(Path(third['learning_databases'][kind]).is_file())
            with closing(sqlite3.connect(first['learning_databases']['skills'])) as database:
                retained = {row[0] for row in database.execute('SELECT task_id FROM skill_experiences')}
            self.assertTrue({first['task_id'], second['task_id']} <= retained)
            with closing(sqlite3.connect(third['learning_databases']['skills'])) as database:
                isolated = {row[0] for row in database.execute('SELECT task_id FROM skill_experiences')}
            self.assertNotIn(first['task_id'], isolated)
            self.assertNotIn(second['task_id'], isolated)
            arguments = json.loads((second_root / 'control/runtime-arguments.json').read_text(encoding='utf-8'))
            self.assertEqual(second['learning_databases']['memory'], arguments[arguments.index('--memory') + 1])
            self.assertEqual(second['learning_databases']['skills'], arguments[arguments.index('--skills') + 1])
            self.assertEqual(second['memory_scope'], arguments[arguments.index('--project-id') + 1])

    def test_project_store_default_and_case_alias_are_project_scoped_without_creating_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / 'workspace'
            workspace.mkdir()
            fake_home = root / 'fake-home'
            with patch('agent_task.Path.home', return_value=fake_home):
                selected = project_storage(workspace)
            expected_key = hashlib.sha256(os.path.normcase(str(workspace.resolve())).encode('utf-8')).hexdigest()[:24]
            self.assertEqual(str(fake_home / '.agi-like/projects' / expected_key), selected['directory'])
            self.assertFalse(fake_home.exists())
            if os.name == 'nt':
                alias = project_storage(Path(str(workspace).upper()), root / 'state')
                original = project_storage(workspace, root / 'state')
                self.assertEqual(original, alias)

    def test_project_store_rejects_workspace_storage_and_linked_control_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / 'workspace'
            workspace.mkdir()
            with self.assertRaisesRegex(IntakeError, 'outside'):
                project_storage(workspace, workspace / 'state')
            selected = project_storage(workspace, root / 'state')
            bucket = Path(selected['directory'])
            bucket.mkdir(parents=True)
            original = root / 'outside-control'
            original.write_bytes(b'preserve outside data')
            memory = Path(selected['memory'])
            try:
                os.link(original, memory)
            except OSError:
                self.skipTest('local filesystem does not permit hard-link fixture')
            with self.assertRaisesRegex(IntakeError, 'hard links'):
                project_storage(workspace, root / 'state')
            self.assertEqual(b'preserve outside data', original.read_bytes())

    def test_no_verifier_or_missing_business_decision_never_launches_execution(self):
        for review, unknowns, expected in [(False, [], 'NEEDS_VERIFIER'),
                                           (True, ['Which approved tax rate applies?'], 'NEEDS_INPUT')]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                proc, report = self.invoke(root, review=review, unknowns=unknowns)
                self.assertEqual(2, proc.returncode)
                self.assertEqual(expected, report['status'])
                self.assertTrue((root / 'control/plan.json').is_file())
                self.assertFalse((root / 'control/contract.json').exists())
                self.assertFalse((root / 'control/control.sqlite3').exists())
                self.assertFalse((root / 'workspace/report.json').exists())
                self.assertFalse((root / 'project-store').exists())

    def test_wrong_artifact_cannot_complete_by_existing_file_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc, report = self.invoke(root, wrong=True)
            self.assertEqual(2, proc.returncode)
            self.assertNotEqual('COMPLETED', report['status'])
            runtime = json.loads((root / 'control/receipt.json').read_text(encoding='utf-8'))
            self.assertNotEqual('COMPLETED', runtime['control_state']['status'])
            self.assertTrue(runtime['independent_reviews'])
            self.assertTrue(all(row['status'] != 'CHALLENGE_PASS' for row in runtime['independent_reviews']))

    def test_generated_contract_cannot_bypass_or_replace_independent_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.invoke(root)
            original = json.loads((root / 'control/runtime-arguments.json').read_text(encoding='utf-8'))
            alternative = root / 'always-pass.py'
            alternative.write_text('import unittest\nclass Weak(unittest.TestCase):\n def test_weak(self): self.assertTrue(True)\n', encoding='utf-8')
            for index, reviewer in enumerate((None, alternative)):
                with self.subTest(reviewer=str(reviewer)):
                    argv = list(original)
                    # Exercise the contract's mandatory review gate itself,
                    # without relying on --auto-verify's argument requirement.
                    argv.remove('--auto-verify')
                    place = argv.index('--review-script')
                    if reviewer is None:
                        del argv[place:place + 2]
                    else:
                        argv[place + 1] = str(reviewer)
                    database = root / ('bypass-' + str(index) + '.sqlite3')
                    argv[argv.index('--db') + 1] = str(database)
                    argv[argv.index('--task-id') + 1] = 'bypass-' + str(index)
                    argv[argv.index('--receipt') + 1] = str(root / ('bypass-' + str(index) + '.json'))
                    proc = subprocess.run([sys.executable, str(ROOT / 'portable-agent-kit/scripts/agent_run.py'), *argv],
                        env={**os.environ, 'CODEX_CLI_PATH': str(root / 'model.py')},
                        capture_output=True, encoding='utf-8', timeout=10)
                    self.assertNotEqual(0, proc.returncode)
                    self.assertIn('bound independent --review-script', proc.stderr)
                    self.assertFalse(database.exists())

    def test_empty_input_plan_preserves_original_goal_without_inventing_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            context = self.context(Path(temp))
            proposal = {**PLAN, 'inputs': []}
            contract = compile_contract(context, proposal, task_id='empty', review_sha256='b' * 64, planner_steps=1)
            self.assertEqual([], contract['inputs'])
            self.assertEqual([], contract['intake']['inputs'])
            self.assertEqual(GOAL, contract['goal'])

    def test_direct_runtime_rejects_goal_input_and_output_baseline_drift_before_model_launch(self):
        variants = [('goal', 'original goal differs'),
                    ('input', 'intake input changed'),
                    ('existing-output', 'intake output baseline changed'),
                    ('appeared-output', 'intake output baseline changed')]
        for variant, expected in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                context = self.context(root)
                workspace = Path(context['workspace'])
                output = workspace / 'report.json'
                if variant == 'existing-output':
                    output.write_text('{"total":0}', encoding='utf-8')
                    context = build_context(GOAL, workspace, ['report.json'], 'human-request:local-test', max_steps=6)
                reviewer = root / 'review.py'
                reviewer.write_text(REVIEW, encoding='utf-8')
                contract = compile_contract(context, PLAN, task_id='bound-intake',
                    review_sha256=hashlib.sha256(reviewer.read_bytes()).hexdigest(), planner_steps=0)
                contract_path = root / 'contract.json'
                contract_path.write_text(json.dumps(contract), encoding='utf-8')
                goal = GOAL
                if variant == 'goal':
                    goal = 'Ignore the original arithmetic request and only make a file.'
                elif variant == 'input':
                    (workspace / 'numbers.json').write_text('{"values":[999]}', encoding='utf-8')
                else:
                    output.write_text('{"total":999}', encoding='utf-8')
                marker = root / 'model-was-launched.txt'
                fake = root / 'forbidden-model.py'
                fake.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("launched")\n', encoding='utf-8')
                database = root / 'control.sqlite3'
                proc = subprocess.run([sys.executable, str(ROOT / 'portable-agent-kit/scripts/agent_run.py'),
                    '--goal', goal, '--workspace', str(workspace), '--contract', str(contract_path),
                    '--db', str(database), '--memory', str(root / 'memory.sqlite3'),
                    '--receipt', str(root / 'receipt.json'), '--task-id', 'bound-intake',
                    '--review-script', str(reviewer), '--max-steps', '6'],
                    env={**os.environ, 'CODEX_CLI_PATH': str(fake)},
                    capture_output=True, encoding='utf-8', timeout=10)
                self.assertNotEqual(0, proc.returncode)
                self.assertIn(expected, proc.stderr)
                self.assertFalse(marker.exists(), 'a drifted intake must fail before launching its decision host')
                self.assertFalse(database.exists(), 'a drifted intake must not create a runnable control task')

    def test_existing_control_root_is_not_replayed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.invoke(root, review=False)
            previous = (root / 'control/intake-receipt.json').read_bytes()
            proc = subprocess.run([sys.executable, str(CLI), '--goal', GOAL,
                '--workspace', str(root / 'workspace'), '--allow-write', '.', '--authorization-ref', 'test',
                '--project-store', str(root / 'project-store'),
                '--control-root', str(root / 'control'), '--plan', str(root / 'plan.json')],
                capture_output=True, encoding='utf-8', timeout=10)
            self.assertEqual(2, proc.returncode)
            self.assertIn('already exists', proc.stderr)
            self.assertEqual(previous, (root / 'control/intake-receipt.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
