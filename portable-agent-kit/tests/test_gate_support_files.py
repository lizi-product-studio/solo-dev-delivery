"""Explicit frozen execution dependencies stay distinct from source obligations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import gate


class GateSupportFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.control = self.root / 'review'
        self.task = self.control / 'task.json'
        (self.work / 'request.md').write_text('Sum the CSV values exactly.\n')
        (self.work / 'tests' / 'data').mkdir(parents=True)
        (self.work / 'tests' / 'data' / 'numbers.csv').write_text('value\n2\n3\n5\n')
        (self.work / 'tests' / 'test_csv_total.py').write_text(
            'import csv\nfrom pathlib import Path\nimport unittest\nimport candidate\n'
            'class ExistingCSVTests(unittest.TestCase):\n'
            ' def test_total(self):\n'
            '  data = Path(__file__).parent / "data" / "numbers.csv"\n'
            '  with data.open(newline="") as stream:\n'
            '   values = [row["value"] for row in csv.DictReader(stream)]\n'
            '  self.assertEqual(candidate.total(values), 10)\n')
        self.support = ['tests/test_csv_total.py', 'tests/data/numbers.csv']

    def start(self, names=None, decision_mode=True):
        return gate.start(self.work, ['request.md'], ['candidate.py'], self.task,
                          decision_mode=decision_mode,
                          support_files=self.support if names is None else names)

    def plan(self):
        self.adapter = self.control / 'checks.py'
        self.adapter.write_text(
            'from pathlib import Path\nimport os\nimport unittest\n'
            'class FrozenChecks(unittest.TestCase):\n'
            ' def test_existing_csv_suite(self):\n'
            '  self.assertEqual(Path.cwd(), Path(os.environ["SOLO_CANDIDATE_ROOT"]))\n'
            '  suite = unittest.defaultTestLoader.discover("tests", pattern="test_csv_total.py")\n'
            '  result = unittest.TestResult()\n'
            '  suite.run(result)\n'
            '  self.assertEqual(result.testsRun, 1)\n'
            '  self.assertTrue(result.wasSuccessful(), str(result.failures + result.errors))\n')
        self.plan_path = self.control / 'plan.json'
        gate.save(self.plan_path, {'test_file': str(self.adapter), 'checks': [
            {'id': 'existing-csv-suite', 'test': 'FrozenChecks.test_existing_csv_suite',
             'sources': ['request.md:1']}], 'outcomes': []})
        return self.plan_path

    def implement(self, correct=True):
        (self.work / 'candidate.py').write_text(
            'def total(values):\n return sum(int(value) for value in values)' +
            ('\n' if correct else ' + 1\n'))

    def ready(self, correct=True):
        self.start()
        gate.seal(self.task, self.plan())
        self.implement(correct)

    def test_existing_unittest_and_csv_execute_in_frozen_candidate(self):
        self.ready()
        result = gate.check(self.task, True)
        self.assertEqual(result['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        self.assertEqual(result['executed_checks'], ['existing-csv-suite'])
        packet = gate.read(result['packet'])
        self.assertEqual(packet['execution_scope'], 'candidate-copy')
        self.assertEqual({item['path'] for item in packet['inputs']},
                         {'request.md', *self.support})
        self.assertEqual([item['path'] for item in packet['outputs']], ['candidate.py'])
        brief = gate.read(self.control / 'brief.json')
        self.assertEqual([item['path'] for item in brief['inputs']], ['request.md'])
        self.assertEqual([item['id'] for item in brief['units']], ['request.md:1'])
        self.assertEqual(gate.check(self.task, True)['executed_checks'], [])

    def test_real_existing_suite_failure_blocks_and_repaired_output_rechecks(self):
        self.ready(False)
        result = gate.check(self.task, True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['failed_outcomes'], ['source@request.md:1'])
        receipts = list(self.control.glob('runs/*/*-replay.json'))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(gate.read(receipts[0])['status'], 'FAIL')
        self.implement()
        self.assertEqual(gate.check(self.task, True)['status'],
                         'DELIVERABLE_WITHIN_DECLARED_SCOPE')

    def test_support_file_mutation_and_disappearance_block_sealing(self):
        self.start()
        self.plan()
        support = self.work / self.support[1]
        support.write_text('value\n20\n')
        with self.assertRaisesRegex(ValueError, 'support files changed'):
            gate.seal(self.task, self.plan_path)
        support.unlink()
        with self.assertRaisesRegex(ValueError, 'support files changed'):
            gate.seal(self.task, self.plan_path)

    def test_pass_is_revoked_by_test_or_data_dependency_changes(self):
        self.ready()
        passed = gate.check(self.task, True)
        self.assertEqual(passed['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        for name in self.support:
            with self.subTest(name=name):
                path = self.work / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                for execute in (False, True):
                    result = gate.check(self.task, execute)
                    self.assertEqual(result['status'], 'BLOCKED')
                    self.assertEqual(result['next_action'], 'RESTART_WITH_CHANGED_SUPPORT_FILES')
                path.unlink()
                self.assertEqual(gate.check(self.task)['status'], 'BLOCKED')
                path.write_bytes(original)
        self.assertEqual(gate.check(self.task)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')

    def test_support_declaration_cannot_be_dropped_after_sealing(self):
        self.ready()
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        task = gate.read(self.task)
        task.pop('support_files')
        gate.save(self.task, task)
        self.assertEqual(gate.check(self.task)['next_action'], 'RESTART_WITH_CHANGED_SUPPORT_FILES')

    def test_support_change_during_check_cannot_release_an_execution_pass(self):
        self.ready()
        assess = gate.challenge.assess

        def change_after_assessment(*args, **kwargs):
            result = assess(*args, **kwargs)
            self.assertEqual(result['status'], 'DECLARED_SCOPE_COVERED')
            (self.work / self.support[1]).write_text('value\n20\n')
            return result

        with patch.object(gate.challenge, 'assess', change_after_assessment):
            result = gate.check(self.task, True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['next_action'], 'RESTART_WITH_CHANGED_SUPPORT_FILES')
        self.assertEqual(gate.read(self.task)['state'], 'CHECKING')

    def test_same_bytes_replaced_with_link_revoke_an_existing_pass(self):
        self.ready()
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        dependency = self.work / self.support[1]
        same_data = self.work / 'same-data.csv'
        same_data.write_bytes(dependency.read_bytes())
        dependency.unlink()
        try:
            dependency.symlink_to(same_data)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        self.assertEqual(gate.check(self.task)['next_action'], 'RESTART_WITH_CHANGED_SUPPORT_FILES')
        with self.assertRaisesRegex(ValueError, 'support files changed'):
            gate.seal(self.task, self.plan_path, 'Retain the original requirements.')

    def test_old_pass_does_not_release_registered_stop_after_support_changes(self):
        home = self.root / 'host'
        gate.start(self.work, ['request.md'], ['candidate.py'], self.task,
                   'support-session', home, support_files=self.support)
        gate.seal(self.task, self.plan())
        self.implement()
        self.assertEqual(gate.check(self.task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
        (self.work / self.support[1]).write_text('value\n20\n')
        result = gate.hook({'hook_event_name': 'Stop', 'session_id': 'support-session'}, home)
        self.assertEqual(result['decision'], 'block')
        self.assertIn('RESTART_WITH_CHANGED_SUPPORT_FILES', result['reason'])
        self.assertTrue(gate.registry(home, 'support-session').exists())

    def test_source_obligation_still_needs_a_check(self):
        (self.work / 'request.md').write_text('Sum the CSV values exactly.\n\nReject invalid values.\n')
        self.start()
        with self.assertRaisesRegex(ValueError, 'unaccounted source units: request.md:3'):
            gate.seal(self.task, self.plan())

    def test_rejects_missing_directory_traversal_absolute_duplicate_and_overlap(self):
        cases = [
            ['missing.csv'], ['tests'], ['tests/../request.md'],
            ['tests\\..\\request.md'], [str(self.work / 'request.md')],
            ['C:\\external\\data.csv'], ['\\\\host\\share\\data.csv'],
            [self.support[0], self.support[0]],
            [self.support[0], 'tests/./test_csv_total.py'],
            ['request.md'], ['candidate.py'], ['.'],
        ]
        for names in cases:
            with self.subTest(names=names):
                with self.assertRaises((OSError, ValueError)):
                    self.start(names)
                self.assertFalse(self.task.exists())
                self.assertFalse((self.control / 'brief.json').exists())

    def test_rejects_support_file_overlapping_an_output_directory(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            gate.start(self.work, ['request.md'], ['tests'], self.task,
                       support_files=self.support)

    def test_rejects_leaf_and_parent_symlinks(self):
        leaf = self.work / 'linked.csv'
        directory = self.work / 'linked-tests'
        try:
            leaf.symlink_to(self.work / self.support[1])
            directory.symlink_to(self.work / 'tests', target_is_directory=True)
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        for name in ('linked.csv', 'linked-tests/data/numbers.csv'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'links or junctions'):
                    self.start([name])

    def test_original_workspace_binding_still_blocks_support_test_access(self):
        path = self.work / self.support[0]
        path.write_text(path.read_text().replace(
            'Path(__file__).parent / "data" / "numbers.csv"',
            'Path(' + repr(str(self.work / self.support[1])) + ')'))
        self.ready()
        result = gate.check(self.task, True)
        self.assertEqual(result['status'], 'BLOCKED')
        receipt = gate.read(next(self.control.glob('runs/*/*-replay.json')))
        self.assertIn('Acceptance must observe the frozen candidate copy', receipt['stderr']['text'])

    def test_legacy_gate_without_support_keeps_existing_task_shape(self):
        gate.start(self.work, ['request.md'], ['candidate.py'], self.task, decision_mode=False)
        task = gate.read(self.task)
        self.assertEqual(task['format'], 'local-delivery-gate/1')
        self.assertNotIn('support_files', task)
        self.assertEqual(gate.check(self.task)['next_action'], 'SELECT_SOURCE_BUSINESS_SCENARIOS')

    def test_start_cli_accepts_repeated_support_file_flags(self):
        env = dict(os.environ)
        env.pop('CODEX_THREAD_ID', None)
        env['CODEX_HOME'] = str(self.root / 'host')
        command = [sys.executable, str(KIT / 'scripts' / 'gate.py'), 'start',
                   '--workspace', str(self.work), '--input', 'request.md',
                   '--output', 'candidate.py', '--task', str(self.task)]
        for name in self.support:
            command += ['--support-file', name]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'NEEDS_SOURCE_CHECKS')
        task = gate.read(self.task)
        self.assertEqual([item['path'] for item in task['support_files']], self.support)
        self.assertTrue(all(len(item['sha256']) == 64 for item in task['support_files']))


if __name__ == '__main__':
    unittest.main()
