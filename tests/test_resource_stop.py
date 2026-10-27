"""Stop uncertainty preserves ownership; liveness checks never signal a worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_resource_engine as fixtures
import review_engine
import resource_engine


class StopOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.units('left')
        original = fixtures.d.git

        def root_transport(repo, *args):
            value = original(repo, *args)
            return str(Path(value).resolve()) if args == ('rev-parse', '--show-toplevel') else value

        # Existing approve() compares Git '/' with Windows '\\' literally.
        # Normalize this fixture transport only; production approval is unchanged.
        with patch.object(fixtures.d, 'git', side_effect=root_transport):
            self.fixture.start()
        self.process = subprocess.Popen([sys.executable, '-u', '-c',
            'import time; print("READY", flush=True); time.sleep(30)'], stdout=subprocess.PIPE)
        self.addCleanup(self.cleanup_process)
        self.assertEqual(b'READY', self.process.stdout.readline().strip())

    def cleanup_process(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.process.stdout.close()

    def dispatch(self, mutate_source=False, **extra):
        def worker(**kwargs):
            kwargs['on_start'](self.process.pid)
            if mutate_source:
                (self.fixture.repo / 'app.txt').write_text('changed fixture candidate\n')
            return dict(status='BLOCKED', execution_status='ERROR', failure_kind='environment',
                        summary='Owned fixture stop remains unconfirmed', usage=None, seconds=0,
                        logs={}, thread_id=None, **extra)
        self.fixture.dispatch(worker)
        return self.fixture.q.resources.rows(self.fixture.t['id'])[0]

    def test_unknown_tree_keeps_active_and_rejects_release_and_duplicate(self):
        f = self.fixture
        row = self.dispatch(process_running=None, process_cleanup={
            'status': 'FAILED', 'tree_stopped': False, 'errors': ['fixture remains alive']})
        self.assertEqual('RUNNING', row['status'])
        self.assertTrue(Path(row['receipt']['result_file']['path']).is_file())
        for action in (lambda: f.q.cancel(f.t['id'], 'fixture cancel'),
                       lambda: f.q.resources.enforce(f.t['id']),
                       lambda: f.q.resources.configure(f.t['id'], 'impl', {}),
                       lambda: f.q.resources.integrated(f.t['id'], 'impl', {
                           'run_id': row['id'], 'evidence': str(f.evidence), 'adopt_partial_patch': True}),
                       lambda: f.q.resources.reconcile(f.t['id'], 'impl', str(f.evidence))):
            with self.assertRaises(ValueError):
                action()
            self.assertIsNone(self.process.poll())
        duplicate = fixtures.SyntheticWorker()
        self.assertEqual([], f.dispatch(duplicate)['executed'])
        self.assertEqual([], duplicate.calls)

    def test_unknown_stop_dominates_scope_and_stale_terminal_classification(self):
        row = self.dispatch(mutate_source=True, process_running=False, process_cleanup={'status': 'FAILED', 'tree_stopped': False},
                            changed_paths=['outside-scope.txt'])
        self.assertEqual('RUNNING', row['status'])
        self.assertTrue(resource_engine.stop_unconfirmed(row['receipt']['result']))

    def test_worker_exception_after_on_start_keeps_ownership(self):
        def worker(**kwargs):
            kwargs['on_start'](self.process.pid)
            raise RuntimeError('fixture lost worker control after spawn')
        self.fixture.dispatch(worker)
        row = self.fixture.q.resources.rows(self.fixture.t['id'])[0]
        self.assertEqual('RUNNING', row['status'])
        self.assertEqual(self.process.pid, row['receipt']['child_pid'])
        self.assertEqual('FAILED', row['receipt']['result']['process_cleanup']['status'])

    def test_exception_before_on_start_does_not_invent_a_live_worker(self):
        def worker(**kwargs):
            raise RuntimeError('fixture failed before spawn')
        self.fixture.dispatch(worker)
        row = self.fixture.q.resources.rows(self.fixture.t['id'])[0]
        self.assertEqual('BLOCKED', row['status'])
        self.assertIsNone(row['receipt']['child_pid'])

    def test_confirmed_stop_and_legacy_receipts_preserve_terminal_behavior(self):
        self.cleanup_process()
        row = self.dispatch(process_running=False, process_cleanup={'status': 'COMPLETE', 'tree_stopped': True})
        self.assertEqual('BLOCKED', row['status'])
        self.assertFalse(resource_engine.stop_unconfirmed({'execution_status': 'COMPLETED'}))
        self.assertFalse(resource_engine.stop_unconfirmed({'execution_status': 'ERROR'}))

    def test_reconcile_requires_tree_attestation_and_readonly_process_checks(self):
        f = self.fixture
        row = self.dispatch(process_running=None, process_cleanup={'status': 'FAILED', 'tree_stopped': False})
        attestation = f.root / 'stopped.json'
        attestation.write_text(json.dumps({'confirmed_stopped_run_ids': [row['id']],
                                          'process_scan_evidence': str(f.evidence)}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '存活'):
            f.q.resources.reconcile(f.t['id'], 'impl', str(attestation))
        self.assertIsNone(self.process.poll())
        with patch.object(resource_engine, 'process_alive', side_effect=OSError('cannot inspect process')):
            with self.assertRaises(OSError):
                f.q.resources.reconcile(f.t['id'], 'impl', str(attestation))
        self.assertEqual('RUNNING', f.q.resources.rows(f.t['id'])[0]['status'])
        self.cleanup_process()
        # The test runner remains alive; simulate its independently confirmed
        # prior exit, while genuinely probing the now-ended owned child.
        real_probe = resource_engine.process_alive
        with patch.object(resource_engine, 'process_alive',
                          side_effect=lambda pid: False if pid == os.getpid() else real_probe(pid)):
            f.q.resources.reconcile(f.t['id'], 'impl', str(attestation))
        self.assertEqual('INTERRUPTED', f.q.resources.rows(f.t['id'])[0]['status'])


class ReadOnlyProcessTests(unittest.TestCase):
    def test_actual_live_process_is_not_signalled_and_exited_process_is_false(self):
        process = subprocess.Popen([sys.executable, '-u', '-c',
            'import time; print("READY", flush=True); time.sleep(15)'], stdout=subprocess.PIPE)
        try:
            self.assertEqual(b'READY', process.stdout.readline().strip())
            for _ in range(5):
                self.assertTrue(review_engine.process_alive(process.pid))
                self.assertIsNone(process.poll())
            process.kill()
            process.wait(timeout=5)
            self.assertFalse(review_engine.process_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
