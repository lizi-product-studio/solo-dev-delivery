"""Ownership/recovery regressions using actual SQLite state and transactions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from control_plane.runtime import ControlPlaneError, DeliveryRuntime


class LeaseRecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = DeliveryRuntime(Path(self.temp.name) / 'control.sqlite3')
        self.task_id = 'recovery-task'
        self.runtime.create_task({
            'task_id': self.task_id, 'goal': 'bounded recovery', 'owner': 'owner',
            'candidate': 'v1', 'authorization_ref': 'isolated-test',
            'scope': ['out.json'], 'excluded': ['external'],
            'policy': {'workspace_write': False},
            'budget': {'max_steps': 1, 'max_retries': 1},
            'acceptance': [{'id': 'A', 'expected': 'actual readback', 'environment': 'isolated'}],
        }, {'evidence.hash': True, 'checkpoint.persist': True})
        self.lease = self.runtime.claim(self.task_id, 'old-writer')

    def expire(self):
        with self.runtime._db() as db:
            db.execute('UPDATE leases SET expires_at=? WHERE task_id=?',
                       (time.time() - 1, self.task_id))

    def proof(self, **changes):
        return {'process_stopped': True, 'task_id': self.task_id,
                'lease_id': self.lease['lease_id'], 'source': 'host-process-observation', **changes}

    def assert_original_lease(self):
        recovery = self.runtime.recover(self.task_id)
        self.assertEqual('RECOVERY_REQUIRED', recovery['status'])
        self.assertEqual(self.lease['lease_id'], recovery['lease_id'])

    def test_expired_claim_cannot_steal_writer_ownership(self):
        self.expire()
        with self.assertRaisesRegex(ControlPlaneError, 'RECLAIM_REQUIRED'):
            self.runtime.claim(self.task_id, 'new-writer')
        self.assert_original_lease()

    def test_missing_or_wrong_proof_binding_preserves_old_lease(self):
        self.expire()
        for proof in ({'process_stopped': True}, self.proof(task_id='other-task'),
                      self.proof(lease_id='other-lease'), self.proof(process_stopped=False)):
            with self.subTest(proof=proof), self.assertRaises(ControlPlaneError):
                self.runtime.reclaim(self.task_id, 'new-writer', stop_proof=proof)
            self.assert_original_lease()

    def test_invalid_owner_or_ttl_never_removes_old_lease(self):
        self.expire()
        for owner, ttl in (('', 300), ('new', 0), ('new', -1), ('new', True), ('new', 0.5)):
            with self.subTest(owner=owner, ttl=ttl), self.assertRaises(ControlPlaneError):
                self.runtime.reclaim(self.task_id, owner, stop_proof=self.proof(), ttl_seconds=ttl)
            self.assert_original_lease()

    def test_reclaim_is_atomic_between_competing_recoverers(self):
        self.expire()
        barrier = threading.Barrier(2)
        def reclaim(owner):
            barrier.wait(timeout=3)
            try:
                return self.runtime.reclaim(self.task_id, owner, stop_proof=self.proof())
            except ControlPlaneError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reclaim, ['new-one', 'new-two']))
        successes = [item for item in results if isinstance(item, dict)]
        self.assertEqual(1, len(successes))
        self.assertNotEqual(self.lease['lease_id'], successes[0]['lease_id'])
        self.assertEqual(successes[0]['lease_id'], self.runtime.recover(self.task_id)['lease_id'])
        events = self.runtime.events(self.task_id)
        self.assertEqual(1, sum(event['operation'] == 'reclaim' for event in events))
        self.assertEqual(self.proof(), events[-1]['request']['stop_proof'])

    def test_budget_exhaustion_does_not_prevent_checkpoint_pause_cancel(self):
        lease_id = self.lease['lease_id']
        state, _ = self.runtime.apply(self.task_id, lease_id, 'start', {'executor': 'old-writer'})
        self.assertEqual(1, state['control_plane']['steps_used'])
        with self.assertRaisesRegex(ControlPlaneError, 'BUDGET_EXHAUSTED'):
            self.runtime.apply(self.task_id, lease_id, 'tool_result',
                               {'tool_call_id': 'new-action', 'status': 'SUCCEEDED'})
        self.runtime.apply(self.task_id, lease_id, 'checkpoint',
                           {'id': 'cp', 'resume_token': 'token', 'cursor': 'safe boundary'})
        state, _ = self.runtime.apply(self.task_id, lease_id, 'pause', {'executor_stopped': True})
        self.assertEqual('PAUSED', state['status'])
        state, _ = self.runtime.cancel(self.task_id, lease_id, executor_stopped=True,
                                       reason='user cancelled at budget boundary')
        self.assertEqual('CANCELLED', state['status'])
        self.assertEqual(1, state['control_plane']['steps_used'])

    def test_release_refuses_active_executor_until_paused(self):
        lease_id = self.lease['lease_id']
        self.runtime.apply(self.task_id, lease_id, 'start', {'executor': 'old-writer'})
        with self.assertRaisesRegex(ControlPlaneError, 'RELEASE_REQUIRES_EXECUTOR_STOP'):
            self.runtime.release(self.task_id, lease_id)
        self.assertEqual('ACTIVE_WRITER', self.runtime.recover(self.task_id)['status'])
        self.runtime.apply(self.task_id, lease_id, 'checkpoint', {'id': 'cp', 'resume_token': 'token'})
        self.runtime.apply(self.task_id, lease_id, 'pause', {'executor_stopped': True})
        self.assertEqual('RELEASED', self.runtime.release(self.task_id, lease_id)['status'])

    def test_budget_exhaustion_still_accepts_readback_and_reconciliation(self):
        root = Path(self.temp.name)
        evidence = root / 'readback.txt'
        evidence.write_text('external action is confirmed successful', encoding='utf-8')
        task_id = 'reconcile-task'
        self.runtime.create_task({
            'task_id': task_id, 'goal': 'readback', 'owner': 'owner', 'candidate': 'v1',
            'authorization_ref': 'local-test', 'scope': ['readback.txt'], 'excluded': ['new-send'],
            'policy': {'workspace_write': False}, 'budget': {'max_steps': 2, 'max_retries': 1},
            'acceptance': [{'id': 'A', 'expected': 'readback exists', 'environment': 'isolated',
                            'assertion': {'type': 'file_exists'}}],
        }, {'evidence.hash': True, 'checkpoint.persist': True})
        lease_id = self.runtime.claim(task_id, 'worker')['lease_id']
        self.runtime.apply(task_id, lease_id, 'start', {'executor': 'worker'})
        state, _ = self.runtime.apply(task_id, lease_id, 'tool_result',
                                       {'tool_call_id': 'call-1', 'status': 'UNKNOWN'})
        self.assertEqual(2, state['control_plane']['steps_used'])
        ref = {'id': 'post-unknown-readback', 'kind': 'readback', 'uri': 'readback.txt',
               'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest(),
               'claim': 'host observed result', 'verdict': 'PASS', 'check_id': 'A',
               'environment': 'isolated', 'tool_call_id': 'call-1', 'observed_outcome': 'SUCCEEDED',
               **{key: state[key] for key in ('task_id', 'revision', 'contract_hash', 'candidate')}}
        self.runtime.apply(task_id, lease_id, 'evidence', ref, evidence_root=root,
                           reviewer_id='independent-host')
        state, _ = self.runtime.reconcile_external(task_id, lease_id, tool_call_id='call-1',
                                                   evidence=ref, outcome='SUCCEEDED', evidence_root=root,
                                                   reviewer_id='independent-host')
        self.assertEqual([], state['unknown_tool_calls'])
        self.assertEqual('RUNNING', state['status'])
        self.assertEqual(2, state['control_plane']['steps_used'])


if __name__ == '__main__':
    unittest.main()
