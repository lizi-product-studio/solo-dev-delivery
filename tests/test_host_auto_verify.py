"""Host completion through the public CLI and labelled local model doubles.

No real model or external service is used. Independent unittest code reads
frozen inputs and outputs; a model finish proposal is deliberately unnecessary.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing


ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'portable-agent-kit'
CLI = KIT / 'scripts/agent_run.py'
sys.path.insert(0, str(KIT))
from agent_runtime import AgentRuntime, MemoryStore, ToolRegistry
from agent_runtime.journal import RunJournal


class HostAutoVerifyTests(unittest.TestCase):
    def prepare(self, root):
        workspace = root / 'workspace'
        workspace.mkdir()
        (workspace / 'TASK.md').write_text(
            'Read both original inputs; sum values into result.json without changing inputs.', encoding='utf-8')
        (workspace / 'source.json').write_text(json.dumps({'values': [20, 22]}), encoding='utf-8')
        review = root / 'review.py'
        review.write_text(
            'import json, unittest\nfrom pathlib import Path\n'
            'class Independent(unittest.TestCase):\n'
            ' def test_actual_total(self):\n'
            '  source=json.loads(Path("source.json").read_text(encoding="utf-8"))\n'
            '  result=json.loads(Path("result.json").read_text(encoding="utf-8"))\n'
            '  self.assertEqual(sum(source["values"]), result["total"])\n', encoding='utf-8')
        contract = {
            'owner': 'host-auto-verify-test', 'candidate': 'synthetic-fixture',
            'authorization_ref': 'local-temporary-test',
            'scope': ['result.json'], 'inputs': ['TASK.md', 'source.json'],
            'excluded': ['external'],
            'policy': {'workspace_write': True, 'shell_required': False},
            'acceptance': [{'id': 'result', 'expected': 'independently checked result artifact',
                'environment': 'local', 'assertion': {'type': 'file_exists', 'path': 'result.json'}}],
        }
        (root / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
        return workspace, review

    def read(self, path):
        return {'action': 'tool', 'name': 'read_json' if path.endswith('.json') else 'read_file',
                'arguments': {'path': path}}

    def write(self, total):
        return {'action': 'tool', 'name': 'write_json',
                'arguments': {'path': 'result.json', 'value': {'total': total}}}

    def invoke(self, root, plan, *, tamper=None, configure=None):
        workspace, review = self.prepare(root)
        if configure:
            configure(workspace, review)
        source = (
            'import json,sys\nfrom pathlib import Path\n'
            'sys.stdin.reconfigure(encoding="utf-8")\nsys.stdout.reconfigure(encoding="utf-8")\n'
            'request=json.loads(sys.stdin.read().split("Request:\\n",1)[1])\n'
            'plan=json.loads(' + repr(json.dumps(plan)) + ')\n'
            'step=request["step"]\n'
            'with Path(' + repr(str(root / 'model-steps.jsonl')) + ').open("a",encoding="utf-8") as out:\n'
            ' out.write(json.dumps({"step":step,"remaining":request["budget"]["steps_remaining"]})+"\\n")\n'
        )
        if tamper == 'input':
            source += ('if step==3:Path(' + repr(str(workspace / 'source.json'))
                       + ').write_text("{\\"values\\":[999]}",encoding="utf-8")\n')
        elif tamper == 'review':
            source += ('if step==3:Path(' + repr(str(review))
                       + ').write_text("import unittest\\nclass Forged(unittest.TestCase):\\n def test_accept(self):pass\\n",encoding="utf-8")\n')
        source += (
            'decision=plan[step-1]\n'
            'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(decision)}}))\n'
            'print(json.dumps({"type":"turn.completed"}))\n'
        )
        model = root / 'fixture_model.py'
        model.write_text(source, encoding='utf-8')
        env = {**os.environ, 'CODEX_CLI_PATH': str(model),
               'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        argv = [sys.executable, str(CLI), '--goal', 'sum actual inputs',
                '--workspace', str(workspace), '--memory', str(root / 'memory.sqlite3'),
                '--contract', str(root / 'contract.json'), '--db', str(root / 'control.sqlite3'),
                '--state-db', str(root / 'state.sqlite3'), '--receipt', str(root / 'receipt.json'),
                '--review-script', str(review), '--auto-verify', '--max-steps', str(len(plan))]
        process = subprocess.run(argv, env=env, cwd=root, capture_output=True,
                                 encoding='utf-8', timeout=30)
        self.assertTrue((root / 'receipt.json').is_file(), process.stderr)
        report = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))
        calls = [json.loads(line) for line in (root / 'model-steps.jsonl').read_text(encoding='utf-8').splitlines()]
        with closing(sqlite3.connect(root / 'memory.sqlite3')) as database:
            successes = database.execute("SELECT count(*) FROM memories WHERE kind='success'").fetchone()[0]
        return process, report, calls, successes

    def assert_host_completed(self, process, report, calls, successes, steps):
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual('COMPLETED', report['control_state']['status'])
        self.assertEqual(steps, report['steps'])
        self.assertEqual(steps, len(calls))
        self.assertEqual(1, successes)
        finishes = [event for event in report['events'] if event['action'] == 'finish']
        self.assertTrue(finishes)
        self.assertTrue(all(event.get('source') == 'host-artifact-review' for event in finishes))

    def rejected_finishes(self, report):
        return [event for event in report['events'] if event['action'] == 'finish'
                and event.get('result', {}).get('status') == 'REJECTED']

    def test_last_budget_step_can_complete_without_a_model_finish(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-last-step-') as temporary:
            root = Path(temporary)
            result = self.invoke(root, [self.read('TASK.md'), self.read('source.json'), self.write(42)])
            self.assert_host_completed(*result, steps=3)
            self.assertEqual(1, result[2][-1]['remaining'])
            self.assertEqual(['CHALLENGE_PASS'], [item['status'] for item in result[1]['independent_reviews']])
            self.assertEqual({'total': 42}, json.loads((root / 'workspace/result.json').read_text(encoding='utf-8')))

    def test_unread_inputs_prevent_review_even_if_the_output_number_is_correct(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-input-read-') as temporary:
            result = self.invoke(Path(temporary), [self.read('TASK.md'), self.write(42)])
            self.assertEqual(2, result[0].returncode)
            self.assertEqual('BUDGET_EXHAUSTED', result[1]['status'])
            self.assertEqual([], result[1]['independent_reviews'])
            self.assertEqual(0, result[3])

    def test_only_a_write_after_all_real_input_reads_triggers_review(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-input-order-') as temporary:
            result = self.invoke(Path(temporary), [self.write(42), self.read('TASK.md'),
                self.read('source.json'), self.write(42)])
            self.assert_host_completed(*result, steps=4)
            self.assertEqual(1, len(result[1]['independent_reviews']))

    def test_four_existing_outputs_can_be_updated_before_the_first_review(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-existing-outputs-') as temporary:
            root = Path(temporary)
            names = ['part-%d.json' % index for index in range(4)]
            def configure(workspace, review):
                for name in names:
                    (workspace / name).write_text('{"total":0}', encoding='utf-8')
                contract_path = root / 'contract.json'
                contract = json.loads(contract_path.read_text(encoding='utf-8'))
                contract['scope'] = names
                contract['acceptance'] = [{
                    'id': 'part-%d' % index, 'expected': 'independently checked updated part',
                    'environment': 'local', 'assertion': {'type': 'file_exists', 'path': name},
                } for index, name in enumerate(names)]
                contract_path.write_text(json.dumps(contract), encoding='utf-8')
                review.write_text(
                    'import json, unittest\nfrom pathlib import Path\n'
                    'class Independent(unittest.TestCase):\n'
                    ' def test_all_updated_parts(self):\n'
                    '  expected=sum(json.loads(Path("source.json").read_text(encoding="utf-8"))["values"])\n'
                    '  for name in ' + repr(names) + ':\n'
                    '   self.assertEqual(expected,json.loads(Path(name).read_text(encoding="utf-8"))["total"])\n',
                    encoding='utf-8')
            plan = [self.read('TASK.md'), self.read('source.json')]
            plan.extend({'action': 'tool', 'name': 'write_json',
                         'arguments': {'path': name, 'value': {'total': 42}}} for name in names)
            result = self.invoke(root, plan, configure=configure)
            self.assert_host_completed(*result, steps=6)
            self.assertEqual(['CHALLENGE_PASS'], [item['status'] for item in result[1]['independent_reviews']])
            self.assertEqual([], self.rejected_finishes(result[1]))
            self.assertEqual(1, result[2][-1]['remaining'])
            for name in names:
                self.assertEqual({'total': 42}, json.loads((root / 'workspace' / name).read_text(encoding='utf-8')))

    def test_wrong_output_is_rejected_then_a_changed_candidate_is_retested(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-repair-') as temporary:
            result = self.invoke(Path(temporary), [self.read('TASK.md'), self.read('source.json'),
                self.write(41), self.write(42)])
            self.assert_host_completed(*result, steps=4)
            self.assertEqual(['FAIL', 'CHALLENGE_PASS'], [item['status'] for item in result[1]['independent_reviews']])
            rejected = self.rejected_finishes(result[1])
            self.assertEqual(1, len(rejected))
            self.assertIn('Independent.test_actual_total', rejected[0]['result']['diagnostics']['failed_tests'])

    def test_identical_wrong_candidates_do_not_repeat_review_or_erase_failure(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-identical-') as temporary:
            result = self.invoke(Path(temporary), [self.read('TASK.md'), self.read('source.json'),
                self.write(41), self.write(41), self.write(41)])
            self.assertEqual(2, result[0].returncode)
            self.assertEqual('BUDGET_EXHAUSTED', result[1]['status'])
            self.assertEqual(['FAIL'], [item['status'] for item in result[1]['independent_reviews']])
            self.assertEqual(0, result[3])
            self.assertEqual(1, len(self.rejected_finishes(result[1])))
            self.assertNotEqual('COMPLETED', result[1]['control_state']['status'])

    def test_auto_verify_requires_an_independent_reviewer_before_startup(self):
        for options in ([], ['--contract', 'missing-contract.json']):
            with self.subTest(options=options), tempfile.TemporaryDirectory(prefix='host-auto-options-') as temporary:
                root = Path(temporary)
                process = subprocess.run([sys.executable, str(CLI), '--goal', 'g',
                    '--workspace', str(root / 'workspace'), '--memory', str(root / 'memory.sqlite3'),
                    '--receipt', str(root / 'receipt.json'), '--auto-verify', *options],
                    cwd=root, capture_output=True, encoding='utf-8', timeout=10)
                self.assertEqual(2, process.returncode)
                self.assertIn('--auto-verify requires --contract and --review-script', process.stderr)
                self.assertEqual([], list(root.iterdir()))

    def test_external_fixture_input_or_reviewer_tampering_blocks_completion(self):
        # These mutations are intentionally performed by the local child fixture
        # outside registered tools, modelling external file changes rather than
        # claiming the subprocess is an OS security sandbox.
        for target, error in (('input', 'STALE_INPUT'), ('review', 'STALE_REVIEWER')):
            with self.subTest(target=target), tempfile.TemporaryDirectory(prefix='host-auto-stale-') as temporary:
                result = self.invoke(Path(temporary), [self.read('TASK.md'), self.read('source.json'),
                    self.write(42)], tamper=target)
                self.assertEqual(2, result[0].returncode)
                self.assertEqual('CONTROL_BLOCKED', result[1]['status'])
                self.assertNotEqual('COMPLETED', result[1]['control_state']['status'])
                self.assertEqual([], result[1]['independent_reviews'])
                self.assertEqual(0, result[3])
                rejected = self.rejected_finishes(result[1])
                errors = [item['error'] for item in rejected[-1]['result']['diagnostics']['bindings']]
                self.assertIn(error, errors)

    def test_unknown_tool_side_effect_never_calls_the_auto_verifier(self):
        with tempfile.TemporaryDirectory(prefix='host-auto-unknown-') as temporary:
            root = Path(temporary)
            effect = root / 'effect.txt'
            tools = ToolRegistry()
            def ambiguous_write():
                effect.write_text('once', encoding='utf-8')
                raise TimeoutError('effect happened before the response was lost')
            tools.register('ambiguous_write', ambiguous_write)
            checks = []
            def verifier(*_):
                checks.append('verify')
                return True
            def ready(_):
                checks.append('ready')
                return True
            verifier.ready = ready
            journal = RunJournal(root / 'state.sqlite3')
            runtime = AgentRuntime(lambda _: {'action': 'tool', 'name': 'ambiguous_write', 'arguments': {}},
                tools, MemoryStore(root / 'memory.sqlite3'), max_steps=1)
            report = runtime.run('write once', task_id='unknown', verifier=verifier,
                                 journal=journal, auto_verify=True)
            self.assertEqual('NEEDS_RECONCILIATION', report.status)
            self.assertEqual([], checks)
            self.assertEqual('once', effect.read_text(encoding='utf-8'))
            self.assertEqual('ACTION_PENDING', journal.load('unknown')['snapshot']['phase'])
            self.assertEqual('NEEDS_RECONCILIATION', runtime.run('write once', task_id='unknown',
                verifier=verifier, journal=journal, auto_verify=True, resume=True).status)
            self.assertEqual([], checks)


if __name__ == '__main__':
    unittest.main()
