from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1] / 'portable-agent-kit'
sys.path.insert(0, str(KIT))
from agent_runtime.scheduler import SchedulerError, TaskScheduler


class TaskSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'queue.sqlite3'
        self.queue = TaskScheduler(self.db)

    def test_dependency_order_unlocks_only_after_completed(self):
        self.queue.enqueue('first', {'goal': 'first'})
        self.queue.enqueue('child', {'goal': 'child'}, ['first'])
        self.queue.enqueue('independent', {'goal': 'third'})
        first = self.queue.claim('worker-1')
        self.assertEqual('first', first['task_id'])
        independent = self.queue.claim('worker-2')
        self.assertEqual('independent', independent['task_id'])
        self.assertIsNone(self.queue.claim('worker-3'))
        self.queue.complete('first', first['claim_token'], {'status': 'COMPLETED', 'evidence': 'host-receipt'})
        self.assertEqual('child', self.queue.claim('worker-3')['task_id'])

    def test_enqueue_cannot_overwrite_and_dependencies_must_exist(self):
        self.queue.enqueue('one', {'value': 1})
        with self.assertRaisesRegex(SchedulerError, 'cannot overwrite'):
            self.queue.enqueue('one', {'value': 2})
        with self.assertRaises(SchedulerError): self.queue.enqueue('bad', {}, ['missing'])
        with self.assertRaises(SchedulerError): self.queue.enqueue('self', {}, ['self'])
        with self.assertRaises(SchedulerError): self.queue.enqueue('duplicate', {}, ['one', 'one'])
        self.assertEqual({'value': 1}, self.queue.get('one')['payload'])
        self.assertEqual(['one'], [row['task_id'] for row in self.queue.list_tasks()])

    def test_real_sqlite_competition_has_only_one_claimant(self):
        self.queue.enqueue('single', {})
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda number: TaskScheduler(self.db).claim('w-' + str(number)), range(8)))
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(1, len(winners))
        self.assertGreaterEqual(len(winners[0]['claim_token']), 32)
        self.assertNotIn('claim_token', self.queue.get('single'))

    def test_unknown_or_failed_task_is_never_retried_or_used_as_completion(self):
        for suffix, status, expected in [('u', 'UNKNOWN', 'UNKNOWN'), ('r', 'NEEDS_RECONCILIATION', 'UNKNOWN'),
                                         ('f', 'FAILED', 'FAILED'), ('v', 'UNVERIFIED', 'FAILED')]:
            parent = 'parent-' + suffix
            self.queue.enqueue(parent, {})
            self.queue.enqueue('child-' + suffix, {}, [parent])
            claim = self.queue.claim('worker')
            self.assertEqual(parent, claim['task_id'])
            result = self.queue.complete(parent, claim['claim_token'], {'status': status})
            self.assertEqual(expected, result['status'])
            self.assertIsNone(self.queue.claim('other-worker'))

    def test_worker_token_isolation_and_result_idempotency(self):
        self.queue.enqueue('one', {})
        claim = self.queue.claim('owner')
        with self.assertRaisesRegex(SchedulerError, 'TOKEN_MISMATCH'):
            self.queue.complete('one', 'wrong', {'status': 'COMPLETED'})
        result = {'status': 'COMPLETED', 'value': 42}
        first = self.queue.complete('one', claim['claim_token'], result)
        count = len(self.queue.events('one'))
        self.assertEqual(first, self.queue.complete('one', claim['claim_token'], result))
        self.assertEqual(count, len(self.queue.events('one')))
        with self.assertRaisesRegex(SchedulerError, 'RESULT_CONFLICT'):
            self.queue.complete('one', claim['claim_token'], {'status': 'COMPLETED', 'value': 99})
        self.assertEqual(result, self.queue.get('one')['result'])

    def test_cancel_pending_and_running_require_different_proof(self):
        self.queue.enqueue('pending', {})
        self.assertEqual('CANCELLED', self.queue.cancel('pending', reason='user withdrew')['status'])
        self.queue.enqueue('running', {})
        claim = self.queue.claim('owner')
        with self.assertRaisesRegex(SchedulerError, 'STOP_PROOF'):
            self.queue.cancel('running', reason='stop', token=claim['claim_token'])
        with self.assertRaisesRegex(SchedulerError, 'STOP_PROOF'):
            self.queue.cancel('running', reason='stop', token=claim['claim_token'],
                              stop_proof={'process_stopped': True, 'task_id': 'other'})
        result = self.queue.cancel('running', reason='host stopped process', token=claim['claim_token'],
                                   stop_proof={'process_stopped': True, 'task_id': 'running'})
        self.assertEqual('CANCELLED', result['status'])
        with self.assertRaisesRegex(SchedulerError, 'RESULT_CONFLICT'):
            self.queue.complete('running', claim['claim_token'], {'status': 'COMPLETED'})

    def test_stale_running_task_is_not_stolen_and_restart_preserves_result(self):
        self.queue.enqueue('stale', {'goal': 'long job'})
        claim = self.queue.claim('original')
        db=sqlite3.connect(self.db)
        try:
            db.execute("UPDATE scheduled_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id='stale'")
            db.commit()
        finally:
            db.close()
        restarted = TaskScheduler(self.db)
        self.assertIsNone(restarted.claim('replacement'))
        self.assertEqual('original', restarted.get('stale')['worker_id'])
        restarted.complete('stale', claim['claim_token'], {'status': 'COMPLETED', 'count': 4})
        script = "import json,sys;sys.path.insert(0,sys.argv[1]);from agent_runtime.scheduler import TaskScheduler;print(json.dumps(TaskScheduler(sys.argv[2]).get('stale')))"
        process = subprocess.run([sys.executable, '-c', script, str(KIT), str(self.db)],
                                  capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual({'status': 'COMPLETED', 'count': 4}, json.loads(process.stdout)['result'])

    def test_payload_is_data_and_invalid_result_does_not_change_state(self):
        self.queue.enqueue('data', {'code': 'raise SystemExit(123)'})
        claim = self.queue.claim('worker')
        self.assertEqual('raise SystemExit(123)', claim['payload']['code'])
        with self.assertRaises(SchedulerError): self.queue.complete('data', claim['claim_token'], {'status': 'PASS'})
        self.assertEqual('RUNNING', self.queue.get('data')['status'])

    def test_bounded_batch_stops_at_limit_and_runs_dependency_after_parent(self):
        self.queue.enqueue('parent', {'value': 21})
        self.queue.enqueue('child', {'value': 42}, ['parent'])
        first = self.queue.run_batch('worker', lambda task: {'status': 'COMPLETED', 'value': task['payload']['value']}, max_tasks=1)
        self.assertEqual(['parent'], [task['task_id'] for task in first])
        self.assertEqual('PENDING', self.queue.get('child')['status'])
        second = self.queue.run_batch('worker', lambda task: {'status': 'COMPLETED', 'value': task['payload']['value']}, max_tasks=3)
        self.assertEqual(['child'], [task['task_id'] for task in second])

    def test_batch_exception_is_unknown_and_invalid_limit_does_not_claim(self):
        self.queue.enqueue('effect', {})
        with self.assertRaises(SchedulerError): self.queue.run_batch('worker', lambda task: {}, max_tasks=0)
        self.assertEqual('PENDING', self.queue.get('effect')['status'])
        target = self.root / 'effect.txt'
        def handler(task):
            target.write_text('effect occurred', encoding='utf-8')
            raise RuntimeError('lost response after side effect')
        result = self.queue.run_batch('worker', handler, max_tasks=1)
        self.assertEqual('UNKNOWN', result[0]['status'])
        self.assertEqual('effect occurred', target.read_text(encoding='utf-8'))
        self.assertEqual([], self.queue.run_batch('replacement', handler, max_tasks=3))


if __name__ == '__main__': unittest.main()
