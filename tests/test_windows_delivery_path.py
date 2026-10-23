"""Real Git/SQLite root checks and local delivery; no path transport mocks.

The queue and subprocess acceptance checks are real. The fixture's declaration
and scope assessment are synthetic and do not attest reviewer identity or a
production delivery.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from review_helpers import assess, bind_all, review_data


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery_path_checks', ROOT / 'scripts/delivery.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class DeliveryRootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / '项目 🚀 repository with spaces'
        self.repo.mkdir()
        self.git('init', '--template=')
        self.git('config', 'user.name', 'Local delivery fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'app.txt').write_text('original\n', encoding='utf-8')
        (self.repo / 'test_probe.py').write_text(
            'import unittest\nfrom pathlib import Path\n'
            'class Probe(unittest.TestCase):\n'
            ' def test_delivery(self):\n'
            '  print("子进程 🚀 UTF8")\n'
            '  self.assertEqual(Path("app.txt").read_text(encoding="utf-8"), "updated\\n")\n',
            encoding='utf-8')
        (self.repo / '.gitignore').write_text('__pycache__/\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic baseline')
        self.q = d.Queue(self.root / 'runtime')
        self.addCleanup(lambda: self.q.db.close())
        brief = self.root / 'brief.md'
        brief.write_text('Local fixture: update app.txt and run its acceptance check.', encoding='utf-8')
        self.t = self.q.ingest(brief)
        self.evidence = self.root / 'scope.txt'
        self.evidence.write_text('Synthetic local scope assessment only.', encoding='utf-8')
        self.contract = {
            'goal': 'Update the local fixture text.', 'repository': str(self.repo),
            'owner': 'fixture', 'approval_ref': 'SYNTHETIC_TEST_ONLY',
            'autonomy': 'local_until_delivery', 'rollback': 'revert fixture commit',
            'scope': ['app.txt'], 'excluded': ['production and external operations'],
            'acceptance': [{'id': 'A', 'expected': 'app.txt contains updated text',
                            'environment': 'local_unit'}],
            'risk_signals': ['copy_change'], 'estimated_business_files': 1,
            'source_resolutions': [{'id': 'manual-brief-review', 'decision': 'local fixture only',
                                    'evidence': 'SYNTHETIC_TEST_ONLY'}],
            'open_business_decisions': [], 'repository_idle_confirmed': True, 'ux_required': False,
        }

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args],
                                       stderr=subprocess.PIPE).decode('utf-8').strip()

    def test_native_git_root_is_accepted_without_transport_normalization(self):
        observed = self.git('rev-parse', '--show-toplevel')
        self.assertTrue(Path(observed).samefile(self.repo))
        if os.name == 'nt':
            # This is the actual Windows Git/Path spelling difference that
            # previously rejected the legitimate repository root.
            self.assertNotEqual(observed, str(self.repo))
        task = self.q.approve(self.t['id'], self.contract)
        self.assertEqual('READY', task['status'])
        self.assertTrue(Path(task['contract']['repository']).samefile(self.repo))

    def test_equivalent_root_spelling_is_accepted(self):
        contract = copy.deepcopy(self.contract)
        contract['repository'] = str(self.repo / 'child' / '..')
        (self.repo / 'child').mkdir()
        if os.name == 'nt':
            contract['repository'] = contract['repository'].upper().replace('\\', '/')
        task = self.q.approve(self.t['id'], contract)
        self.assertTrue(Path(task['contract']['repository']).samefile(self.repo))

    def test_subdirectory_is_rejected_and_task_remains_draft(self):
        child = self.repo / 'child'
        child.mkdir()
        contract = dict(self.contract, repository=str(child))
        with self.assertRaisesRegex(ValueError, 'Git'):
            self.q.approve(self.t['id'], contract)
        self.assertEqual('DRAFT', self.q.task(self.t['id'])['status'])

    def test_distinct_git_work_tree_is_rejected_without_mocking_git(self):
        other = self.root / 'other repository'
        other.mkdir()
        subprocess.run(['git', '-C', str(other), 'init', '--template='],
                       check=True, capture_output=True)
        # core.worktree changes Git's actual root resolution. Even though
        # both directories exist and contain Git metadata, they are distinct.
        self.git('config', 'core.worktree', str(other))
        self.assertTrue(Path(self.git('rev-parse', '--show-toplevel')).samefile(other))
        self.assertFalse(self.repo.samefile(other))
        with self.assertRaisesRegex(ValueError, 'Git'):
            self.q.approve(self.t['id'], self.contract)
        self.assertEqual('DRAFT', self.q.task(self.t['id'])['status'])

    def test_real_edit_checks_and_persistent_local_delivery(self):
        if os.environ.get('SOLO_EXPECT_LEGACY_UTF8_OFF'):
            self.assertEqual(0, sys.flags.utf8_mode)
        task = self.q.approve(self.t['id'], self.contract)
        self.assertEqual('QUICK_PATCH', task['contract']['route']['task_mode'])
        assess(self.q, task, 'fixture-main', self.evidence)
        self.q.claim(task['id'], 'fixture-main')

        (self.repo / 'app.txt').write_text('updated\n', encoding='utf-8')
        self.git('add', 'app.txt')
        self.git('commit', '-qm', 'deliver fixture text update')
        commit = self.git('rev-parse', 'HEAD')
        bind_all(self.q, task, 'fixture-main')
        self.q.reviews.run(task['id'], 'fixture-main')
        for run in self.q.reviews.load(task['id'])['runs']:
            observed = Path(run['evidence']['path']).read_text(encoding='utf-8')
            self.assertTrue(Path(json.loads(observed.splitlines()[0])['cwd']).samefile(self.repo))
            self.assertIn('子进程 🚀 UTF8', observed)

        acceptance = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v', 'test_probe.py'],
                                    cwd=str(self.repo), capture_output=True, text=True,
                                    encoding='utf-8', timeout=20,
                                    env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))
        self.assertEqual(0, acceptance.returncode, acceptance.stdout + acceptance.stderr)
        self.assertIn('Ran 1 test', acceptance.stderr)
        evidence = self.root / 'actual-acceptance.txt'
        evidence.write_text(acceptance.stdout + acceptance.stderr, encoding='utf-8')
        self.q.reviews.record(task['id'], 'fixture-main',
                              review_data(self.q, task, evidence, commit))
        report = {
            'contract_hash': task['contract_hash'], 'commit': commit,
            'environment': 'local', 'open_p0_p1': [],
            'checks': [{'id': 'A', 'status': 'PASS', 'environment': 'local_unit',
                        'observed': (self.repo / 'app.txt').read_text(encoding='utf-8'),
                        'evidence': str(evidence)}],
            'artifacts': [str(evidence)],
        }
        path = self.root / 'report.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual('REVIEW', self.q.submit(task['id'], 'fixture-main', path)['status'])
        self.q.db.close()
        self.q = d.Queue(self.root / 'runtime')
        self.assertEqual('REVIEW', self.q.task(task['id'])['status'])
        self.assertEqual('DELIVERED', self.q.finish(task['id'], 'fixture-main')['status'])
        self.assertEqual('updated\n', (self.repo / 'app.txt').read_text(encoding='utf-8'))
        self.assertEqual('', self.git('status', '--porcelain'))
        kinds = [row[0] for row in self.q.db.execute(
            'SELECT kind FROM events WHERE task_id=? ORDER BY seq', (task['id'],))]
        for expected in ('IMPORTED_AS_DRAFT', 'APPROVED', 'CLAIMED', 'SUBMITTED', 'LOCAL_DELIVERED'):
            self.assertIn(expected, kinds)

    @unittest.skipUnless(os.name == 'nt', 'Native Windows legacy text encoding')
    def test_legacy_parent_and_emoji_worktree_run_complete_delivery(self):
        environment = dict(os.environ, PYTHONUTF8='0', SOLO_EXPECT_LEGACY_UTF8_OFF='1')
        environment.pop('PYTHONIOENCODING', None)
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests',
             '-p', 'test_windows_delivery_path.py', '-k', 'test_real_edit', '-v'],
            cwd=str(ROOT), env=environment, capture_output=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        self.assertIn(b'Ran 1 test', result.stderr)


if __name__ == '__main__':
    unittest.main()
