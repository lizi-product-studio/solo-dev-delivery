"""Resource policy regressions with temporary Git/SQLite and synthetic workers only.

The injected worker returns host-shaped receipts; it never starts Codex or calls a
model. These tests establish scheduler behavior, not real reviewer identity.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import sys
from unittest.mock import patch

from review_helpers import assess, bind_all, review_data


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery_resources', ROOT / 'scripts/delivery.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)
import resource_engine as engine


class SyntheticWorker:
    """Thread-safe test double with observable starts and optional controlled faults."""

    def __init__(self, *, status='PASS', failure_kind='none', execution_status='COMPLETED',
                 hook=None, barrier=None, usage=True, emit_patch=False, empty_artifacts=False):
        self.status = status
        self.failure_kind = failure_kind
        self.execution_status = execution_status
        self.hook = hook
        self.barrier = barrier
        self.usage = usage
        self.emit_patch = emit_patch
        self.empty_artifacts = empty_artifacts
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def __call__(self, **kwargs):
        packet = json.loads(kwargs['prompt'].split('\n', 1)[1])
        with self.lock:
            self.calls.append({**kwargs, 'packet': packet})
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            kwargs['on_start'](os.getpid())
            if self.barrier:
                self.barrier.wait(timeout=5)
            if self.hook:
                self.hook(kwargs, packet)
            output = Path(kwargs['output_dir'])
            log = output / 'synthetic-worker.log'
            log.write_text('Synthetic fixture execution for ' + packet['unit']['id'])
            result = {
                'status': self.status, 'summary': 'Synthetic fixture only; no model invocation.',
                'failure_kind': self.failure_kind, 'execution_status': self.execution_status,
                'thread_id': 'synthetic-' + output.name,
                'usage': {'input_tokens': 100, 'cached_input_tokens': 30, 'output_tokens': 20}
                         if self.usage else None,
                'seconds': 1.0, 'logs': {'stdout': engine.proof(log)}, 'patch': None,
                'changed_paths': [],
                'observations': [{'check_id': cid, 'observed': 'Synthetic observation for ' + cid}
                                 for cid in packet.get('review', {}).get('checks', {})],
                'findings': [], 'resolved_findings': [], 'uncovered': [],
            }
            if self.emit_patch:
                change = output / 'changes.patch'
                change.write_text('diff --git a/left/value.txt b/left/value.txt\n'
                                  '--- a/left/value.txt\n+++ b/left/value.txt\n'
                                  '@@ -1 +1 @@\n-left\n+proposed\n')
                result['patch'] = engine.proof(change)
                result['changed_paths'] = ['left/value.txt']
            if self.empty_artifacts:
                for name in ('stderr.log', 'changes.patch'):
                    path = output / name
                    path.write_bytes(b'')
                    ref = {'path': str(path), 'sha256': hashlib.sha256(b'').hexdigest()}
                    if name == 'stderr.log':
                        result['logs']['stderr'] = ref
                    else:
                        result['patch'] = ref
            return result
        finally:
            with self.lock:
                self.active -= 1


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repository'
        self.repo.mkdir()
        self.git('init', '--template=')
        self.git('config', 'user.name', 'Resource fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        files = {
            'app.txt': 'original\n', 'left/value.txt': 'left\n', 'right/value.txt': 'right\n',
            '.gitignore': '__pycache__/\n',
            'test_probe.py': "import unittest\nfrom pathlib import Path\n"
                             "class Probe(unittest.TestCase):\n"
                             " def test_preserved(self):\n"
                             "  self.assertEqual(Path('app.txt').read_text(), 'original\\n')\n",
        }
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic baseline')
        self.q = d.Queue(self.root / 'runtime')
        self.addCleanup(lambda: self.q.db.close())
        self.evidence = self.root / 'fixture-evidence.txt'
        self.evidence.write_text('Synthetic scope assessment; not a real independent reviewer receipt.')
        brief = self.root / 'brief.md'
        brief.write_text('Resource scheduler synthetic fixture.')
        self.t = self.q.ingest(brief)
        self.c = {
            'goal': 'Preserve synthetic source while exercising bounded scheduling.',
            'repository': str(self.repo), 'owner': 'fixture', 'approval_ref': 'SYNTHETIC_ONLY',
            'autonomy': 'local_until_delivery', 'rollback': 'revert fixture commit',
            'scope': ['app.txt', 'left', 'right', 'test_probe.py'], 'excluded': ['production'],
            'acceptance': [{'id': 'A', 'expected': 'app stays original', 'environment': 'isolated'}],
            'risk_signals': ['copy_change'], 'estimated_business_files': 1,
            'source_resolutions': [{'id': 'manual-brief-review', 'decision': 'fixture only',
                                    'evidence': 'isolated test setup'}],
            'open_business_decisions': [], 'repository_idle_confirmed': True, 'ux_required': False,
            'review_components': [
                {'id': 'main', 'paths': ['app.txt', 'test_probe.py', '.gitignore'], 'depends_on': []},
                {'id': 'left', 'paths': ['left'], 'depends_on': []},
                {'id': 'right', 'paths': ['right'], 'depends_on': []},
            ],
        }
        # Availability alone is stubbed; every run additionally receives an explicit worker.
        self.which = patch.object(engine.shutil, 'which', return_value='/synthetic/never-executed-codex')
        self.which.start()
        self.addCleanup(self.which.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def units(self, *components, complexity='moderate', uncertainty='clear'):
        self.c['resource_inputs'] = {
            'complexity': complexity, 'uncertainty': uncertainty,
            'units': [{'id': cid + '-work', 'goal': 'Inspect synthetic ' + cid,
                       'components': [cid], 'depends_on': []} for cid in components],
        }

    def start(self):
        self.t = self.q.approve(self.t['id'], self.c)
        assess(self.q, self.t, 'impl', self.evidence)
        self.q.claim(self.t['id'], 'impl')
        return self.q.resources.plan(self.t['id'])

    def dispatch(self, worker, phase='work'):
        return self.q.resources.run(self.t['id'], 'impl', phase, worker=worker)

    def quota(self, remaining=80):
        return self.q.resources.set_quota({'source': 'codex_app_usage_limits',
                                          'remaining_percent': remaining, 'observed_at': time.time()})

    def policy(self, **changes):
        # Reduced limits keep budget regressions small without inventing run receipts.
        with self.q.transaction():
            state = self.q.resources.load(self.t['id'])
            state['policy'].update(changes)
            self.q.resources.save(self.t['id'], state)

    def live_fixture(self, steps=40, delay=0.05, checkpoint=0.15, observation=0.05):
        """Real subprocess and queue, explicitly a synthetic CLI, never a model call."""
        self.units('left');self.start()
        self.policy(timeout_seconds=checkpoint,observation_seconds=observation)
        cli=self.root/'synthetic-live-cli'
        cli.write_text('#!'+sys.executable+'\n'+r'''
import json,sys,time,signal
from pathlib import Path
sys.stdin.read()
args=sys.argv[1:];out=Path(args[args.index('-o')+1])
signal.signal(signal.SIGTERM,lambda *a: (time.sleep(.2),sys.exit(0)))
print(json.dumps({'type':'thread.started','thread_id':'synthetic-live-process'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
for n in range(40):
    Path('left/value.txt').write_text('completed fixture step '+str(n))
    print(json.dumps({'type':'item.completed','item':{'id':str(n),'type':'file_change','status':'completed'}}),flush=True)
    time.sleep(.05)
out.write_text(json.dumps({'status':'PASS','summary':'Synthetic CLI fixture only','failure_kind':'none','observations':[],
                          'findings':[],'resolved_findings':[],'uncovered':[]}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
''')
        cli.write_text(cli.read_text().replace('range(40)', 'range('+str(steps)+')').replace('time.sleep(.05)', 'time.sleep('+str(delay)+')'))
        cli.chmod(0o755);engine.shutil.which.return_value=str(cli)
        self.live_errors=[]
        def launch():
            queue=d.Queue(self.q.root)
            try:queue.resources.run(self.t['id'],'impl','work')
            except Exception as exc:self.live_errors.append(exc)
            finally:queue.db.close()
        thread=threading.Thread(target=launch);thread.start()
        self.addCleanup(lambda:thread.join(timeout=8))
        return thread

    def live_observation(self, sequence=0, min_completed=0, needs_decision=False):
        until=time.monotonic()+8
        while time.monotonic()<until:
            rows=self.q.resources.observe(self.t['id'],record=True)['runs']
            for row in rows:
                p=row.get('progress') or {}
                if (row.get('observation_id') and p.get('decision_sequence')==sequence and p.get('process_running')
                        and p.get('completed_items',0)>=min_completed and (not needs_decision or p.get('needs_decision'))):
                    return row
            time.sleep(.02)
        self.fail('No live host observation: '+str(self.live_errors))

    def live_decision(self, observation, action='continue'):
        return {'run_id':observation['run_id'],'observation_id':observation['observation_id'],
                'action':action,'next_checkpoint_seconds':3,
                'progress_assessment':'Fixture actually wrote completed steps; original process is alive',
                'remaining_work':'Finish the bounded fixture loop','reason':'Explicit synthetic parent judgment',
                **({'adjustment':'Inspect preserved patch then narrow next unit'} if action=='adjust' else {})}

    def test_live_parent_continue_is_durable_idempotent_and_same_pid(self):
        thread=self.live_fixture()
        observation=self.live_observation(min_completed=4,needs_decision=True);old=self.live_observation()
        self.assertTrue(observation['progress']['needs_decision'])
        payload=self.live_decision(observation)
        first=self.q.resources.decide(self.t['id'],'impl',payload)
        self.q.db.close();self.q=d.Queue(self.root/'runtime')
        again=self.q.resources.decide(self.t['id'],'impl',payload)
        self.assertTrue(again['idempotent']);self.assertEqual(first['decision'],again['decision'])
        applied=self.live_observation(sequence=1)
        self.assertEqual(observation['progress']['child_pid'],applied['progress']['child_pid'])
        with self.assertRaisesRegex(ValueError,'旧决定'):
            self.q.resources.decide(self.t['id'],'impl',self.live_decision(old))
        thread.join(timeout=8);self.assertFalse(thread.is_alive());self.assertFalse(self.live_errors)
        row=self.q.resources.rows(self.t['id'])[0]
        self.assertEqual('SUCCESS',row['status'])
        self.assertEqual('ACKNOWLEDGED',self.q.resources.observe(self.t['id'])['runs'][0]['decisions'][0]['status'])
        self.assertEqual(1,len(self.q.resources.rows(self.t['id'])))

    def test_decision_rejects_wrong_owner_unrecorded_and_expired_observations(self):
        thread=self.live_fixture();observation=self.live_observation();payload=self.live_decision(observation)
        with self.assertRaises(ValueError):self.q.resources.decide(self.t['id'],'other-owner',payload)
        with self.assertRaisesRegex(ValueError,'宿主观察'):
            self.q.resources.decide(self.t['id'],'impl',{**payload,'observation_id':'invented'})
        with self.q.transaction():
            self.q.db.execute('UPDATE resource_observations SET created=? WHERE id=?',(time.time()-181,observation['observation_id']))
        with self.assertRaisesRegex(ValueError,'过期'):self.q.resources.decide(self.t['id'],'impl',payload)
        thread.join(timeout=8);self.assertFalse(thread.is_alive());self.assertFalse(self.live_errors)
        self.assertEqual('SUCCESS',self.q.resources.rows(self.t['id'])[0]['status'])

    def test_explicit_adjust_preserves_work_and_lock_until_stopped(self):
        thread=self.live_fixture();observation=self.live_observation(min_completed=3)
        with self.q.transaction():
            self.q.resources.decide(self.t['id'],'impl',self.live_decision(observation,'adjust'))
            self.assertIn(self.q.resources.rows(self.t['id'])[0]['status'],engine.ACTIVE)
            with self.assertRaises(ValueError):self.q.resources.configure(self.t['id'],'impl',{})
            with self.assertRaises(ValueError):self.q.cancel(self.t['id'],'Synthetic premature cancellation')
        thread.join(timeout=8);self.assertFalse(thread.is_alive());self.assertFalse(self.live_errors)
        result=self.q.resources.rows(self.t['id'])[0]['receipt']['result']
        self.assertEqual('PARENT_HANDOFF',result['execution_status'])
        self.assertEqual('adjust',result['parent_decision']['action'])
        self.assertTrue(Path(result['patch']['path']).stat().st_size>0)

    def test_time_reassessment_preserves_usage_and_allows_next_bounded_unit(self):
        self.units('left');self.start();worker=SyntheticWorker(status='BLOCKED',failure_kind='reasoning')
        self.dispatch(worker);self.policy(max_elapsed_seconds=1)
        self.assertIn('TIME_REVIEW_REQUIRED',self.q.resources.plan(self.t['id'])['blockers'])
        state=self.q.resources.load(self.t['id'])
        self.q.resources.configure(self.t['id'],'impl',{'contract_hash':state['contract_hash'],
            'reason':'Parent inspected completed fixture and authorizes the next bounded stage',
            'evidence':str(self.evidence),'inputs':state['inputs'],
            'time_review':{'observed_worker_seconds':1.0,'next_review_seconds':60}})
        self.assertEqual(1,self.q.resources.plan(self.t['id'])['usage']['worker_seconds'])
        self.dispatch(worker);self.assertEqual(2,len(worker.calls))

    def test_legacy_five_minute_default_becomes_observation_not_deadline(self):
        self.units('left');self.start()
        self.policy(version=1,timeout_seconds=300)
        worker=SyntheticWorker();self.dispatch(worker)
        self.assertEqual(1800,worker.calls[0]['timeout'])
        self.assertEqual(300,worker.calls[0]['observation_seconds'])
        receipt=self.q.resources.rows(self.t['id'])[0]['receipt']
        self.assertEqual('phase_and_complexity',receipt['run_limits']['source'])
        # Historical stored policy is not silently overwritten.
        self.assertEqual(300,self.q.resources.load(self.t['id'])['policy']['timeout_seconds'])

    def test_explicit_run_budget_preserved_and_recorded_before_launch(self):
        self.units('left');self.c['resource_inputs']['run_budget_seconds']=900;self.start()
        worker=SyntheticWorker();self.dispatch(worker)
        self.assertEqual(900,worker.calls[0]['timeout'])
        self.assertEqual('main_agent_configured',self.q.resources.rows(self.t['id'])[0]['receipt']['run_limits']['source'])
        self.assertEqual(120,engine.run_limits({**engine.POLICY,'timeout_seconds':120},'moderate','implement')['checkpoint_seconds'])

    def test_live_observation_is_read_only_and_not_a_stop_command(self):
        self.units('left');self.start()
        def live(kwargs,packet):
            path=Path(kwargs['output_dir'])/'progress.json'
            path.write_text(json.dumps({'state':'QUIET_NEEDS_REVIEW','observed_at':time.time(),
                                        'active_tools':1,'quiet_seconds':400}))
            observer=d.Queue(self.q.root)
            try:
                observed=observer.resources.observe(self.t['id'])['runs'][0]
                self.assertEqual('RUNNING',observed['status'])
                self.assertEqual('QUIET_NEEDS_REVIEW',observed['progress']['state'])
                self.assertFalse(observed['progress']['stale'])
            finally:observer.db.close()
        worker=SyntheticWorker(hook=live);self.dispatch(worker)
        self.assertEqual('SUCCESS',self.q.resources.rows(self.t['id'])[0]['status'])

    def diagnose(self, rid, kind):
        return self.q.resources.feedback(self.t['id'], 'impl', {
            'run_id': rid, 'kind': kind, 'reason': 'Observed synthetic ' + kind,
            'evidence': str(self.evidence),
        })

    def pass_checks(self):
        bind_all(self.q, self.t, 'impl')
        self.q.reviews.run(self.t['id'], 'impl')

    def report(self):
        review = self.q.reviews.load(self.t['id'])['review'] or {}
        data = {'contract_hash': self.t['contract_hash'], 'commit': d.frozen_commit(self.repo),
                'environment': 'local', 'open_p0_p1': [],
                'checks': [{'id': 'A', 'status': 'PASS', 'environment': 'isolated',
                            'observed': 'Synthetic local probe passed.', 'evidence': str(self.evidence)}],
                'review': {'reviewer': review.get('actor', 'synthetic-reviewer'), 'result': 'PASS',
                           'evidence': str(self.evidence)}, 'artifacts': [str(self.repo / 'app.txt')]}
        path = self.root / 'report.json'
        path.write_text(json.dumps(data))
        return path

    def test_quick_patch_dispatches_zero_children(self):
        plan = self.start()
        worker = SyntheticWorker()
        result = self.dispatch(worker)
        self.assertEqual([], plan['units'])
        self.assertFalse(plan['independent_review_required'])
        self.assertEqual([], result['executed'])
        self.assertEqual([], worker.calls)
        self.assertEqual(0, result['plan']['usage']['calls'])

    def test_risk_raises_review_effort_and_keeps_independent_floor(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.start()
        ordinary = next(u for u in self.q.resources.plan(self.t['id'])['units'] if u['role'] == 'review')
        assessment = self.q.reviews.plan(self.t['id'])
        self.q.reviews.assess(self.t['id'], 'impl', {
            'revision': assessment['revision'], 'contract_hash': self.t['contract_hash'],
            'risk_reasoning': 'Synthetic money-risk expansion.', 'scope_reasoning': 'Same fixture files.',
            'acknowledged_checks': list(assessment['checks']), 'unresolved': [],
            'additional_risk_signals': ['payment'], 'evidence': str(self.evidence),
        })
        plan = self.q.resources.plan(self.t['id'])
        risky = next(u for u in plan['units'] if u['role'] == 'review')
        self.assertEqual('standard', ordinary['profile'])
        self.assertEqual('deep', risky['profile'])
        self.assertNotEqual((ordinary['model'], ordinary['effort']), (risky['model'], risky['effort']))
        self.assertTrue(plan['independent_review_required'])
        self.assertTrue(plan['quality_floor_unchanged'])
        self.git('commit', '--allow-empty', '-qm', 'next risky candidate')
        next_review = next(u for u in self.q.resources.plan(self.t['id'])['units'] if u['role'] == 'review')
        self.assertEqual(risky['profile'], next_review['profile'])

    def test_async_obligation_does_not_force_every_unit_to_highest_profile(self):
        self.c['risk_signals'] = ['api_delta']
        self.units('left')
        plan = self.start()
        units = {u['role']: u for u in plan['units']}
        self.assertIn('RISK:async', self.q.reviews.plan(self.t['id'])['checks'])
        self.assertEqual('ordinary', plan['risk'])
        self.assertEqual('light', units['implement']['profile'])
        self.assertEqual('standard', units['review']['profile'])
        self.assertTrue(plan['independent_review_required'])

    def test_technical_unknown_dispatches_light_exploration_before_implementation(self):
        self.units('left', uncertainty='technical')
        self.start()
        worker = SyntheticWorker()
        result = self.dispatch(worker)
        self.assertEqual(['explore'], [c['role'] for c in worker.calls])
        self.assertEqual(engine.PROFILES['light']['model'], worker.calls[0]['model'])
        self.assertEqual('SUCCESS', result['runs'][0]['status'])
        self.assertEqual('', self.git('status', '--porcelain'))

    def test_business_unknown_blocks_before_any_worker_starts(self):
        self.units('left', uncertainty='business')
        self.start()
        worker = SyntheticWorker()
        with self.assertRaises(ValueError):
            self.dispatch(worker)
        self.assertEqual([], worker.calls)
        self.assertEqual([], self.q.resources.rows(self.t['id']))

    def test_two_completed_reasoning_failures_upgrade_next_attempt_then_stop(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker(status='BLOCKED', failure_kind='reasoning')
        initial = self.q.resources.plan(self.t['id'])['units'][0]
        for index in range(2):
            result = self.dispatch(worker)
            plan = self.diagnose(result['executed'][0], 'reasoning')
            if index == 0:
                self.assertEqual(initial['profile'], plan['units'][0]['profile'])
        self.assertNotEqual(initial['profile'], plan['units'][0]['profile'])
        self.dispatch(worker)
        self.assertEqual(worker.calls[0]['model'], worker.calls[1]['model'])
        self.assertTrue(worker.calls[1]['packet']['previous_attempt']['diagnosis'])
        self.assertNotEqual((worker.calls[1]['model'], worker.calls[1]['effort']),
                            (worker.calls[2]['model'], worker.calls[2]['effort']))
        with self.assertRaises(ValueError):
            self.dispatch(worker)
        self.assertEqual(3, len(worker.calls))
        self.git('commit', '--allow-empty', '-qm', 'next candidate after diagnosis')
        reset = self.q.resources.plan(self.t['id'])['units'][0]
        self.assertEqual(initial['profile'], reset['profile'])
        self.dispatch(worker)
        self.assertEqual(4, len(worker.calls))
        self.assertEqual((worker.calls[0]['model'], worker.calls[0]['effort']),
                         (worker.calls[-1]['model'], worker.calls[-1]['effort']))

    def test_environment_failure_requires_resolution_and_does_not_upgrade(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker(status='BLOCKED', failure_kind='environment', execution_status='ERROR')
        original = self.q.resources.plan(self.t['id'])['units'][0]['profile']
        for index in range(2):
            result = self.dispatch(worker)
            rid = result['executed'][0]
            with self.assertRaises(ValueError):
                self.diagnose(rid, 'reasoning')
            self.diagnose(rid, 'environment')
            with self.assertRaises(ValueError):
                self.dispatch(worker)
            self.assertEqual(index + 1, len(worker.calls))
            plan = self.diagnose(rid, 'resolved')
            self.assertEqual(original, plan['units'][0]['profile'])
        self.assertEqual(worker.calls[0]['model'], worker.calls[1]['model'])

    def test_unknown_stale_and_future_quota_keep_serial_dispatch(self):
        self.units('left', 'right')
        self.start()
        path = self.q.root / 'resource-quota.json'
        for timestamp in (None, time.time() - 301, time.time() + 60):
            with self.subTest(timestamp=timestamp):
                if timestamp is not None:
                    path.write_text(json.dumps({'source': 'codex_app_usage_limits',
                                                'remaining_percent': 100, 'observed_at': timestamp}))
                plan = self.q.resources.plan(self.t['id'])
                self.assertEqual('UNKNOWN', plan['quota']['state'])
                self.assertEqual(1, plan['parallel_cap'])
        worker = SyntheticWorker()
        self.dispatch(worker)
        self.assertEqual(1, len(worker.calls))
        self.assertEqual(1, self.quota(10)['parallel_cap'])

    def test_exhausted_quota_blocks_without_consuming_attempt(self):
        self.units('left')
        self.start()
        self.quota(0)
        worker = SyntheticWorker()
        with self.assertRaises(ValueError):
            self.dispatch(worker)
        self.assertEqual([], worker.calls)
        self.assertEqual([], self.q.resources.rows(self.t['id']))

    def test_available_quota_runs_at_most_two_independent_units_concurrently(self):
        self.units('left', 'right', 'main', complexity='complex')
        self.start()
        self.quota()
        worker = SyntheticWorker(barrier=threading.Barrier(2))
        result = self.dispatch(worker)
        self.assertEqual(2, worker.peak)
        self.assertEqual(2, len(result['executed']))
        self.assertEqual(2, len(worker.calls))
        self.assertEqual({'left-work', 'right-work'}, {c['packet']['unit']['id'] for c in worker.calls})
        self.assertTrue(all(r['status'] == 'SUCCESS' for r in result['runs']))

    def test_overlapping_components_are_not_dispatched_together(self):
        self.c['review_components'].append({'id': 'nested', 'paths': ['left/value.txt'], 'depends_on': []})
        self.units('left', 'nested')
        self.start()
        self.quota()
        nested_results = []

        def dispatch_while_active(_kwargs, _packet):
            queue = d.Queue(self.q.root)
            try:
                nested_worker = SyntheticWorker()
                nested = queue.resources.run(self.t['id'], 'impl', 'work', worker=nested_worker)
                nested_results.append((nested['executed'], len(nested_worker.calls)))
            finally:
                queue.db.close()

        worker = SyntheticWorker(hook=dispatch_while_active)
        result = self.dispatch(worker)
        self.assertEqual(1, len(result['executed']))
        self.assertEqual(1, len(worker.calls))
        self.assertEqual([([], 0)], nested_results)
        self.assertEqual('SUCCESS', result['runs'][0]['status'])

    def test_review_reserve_blocks_work_but_allows_required_review(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.units('left')
        self.start()
        self.policy(max_calls=2, review_reserve=2)
        worker = SyntheticWorker()
        with self.assertRaises(ValueError):
            self.dispatch(worker)
        self.assertEqual([], worker.calls)
        self.pass_checks()
        result = self.dispatch(worker, 'review')
        self.assertEqual(1, len(result['executed']))
        self.assertEqual('review', worker.calls[0]['role'])
        self.assertTrue(self.q.reviews.plan(self.t['id'])['delivery_allowed'])

    def test_last_available_work_slot_is_used_without_spending_review_reserve(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.units('left', 'right')
        self.start()
        self.quota()
        self.policy(max_calls=3, review_reserve=2)
        worker = SyntheticWorker()
        result = self.dispatch(worker)
        self.assertEqual(1, len(result['executed']))
        self.assertEqual(1, len(worker.calls))
        self.assertEqual(2, result['plan']['limits']['max_calls'] - result['plan']['usage']['calls'])

    def test_observed_tokens_time_and_total_calls_are_enforced(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker(status='BLOCKED', failure_kind='reasoning')
        result = self.dispatch(worker)
        self.assertEqual(120, result['plan']['usage']['known_tokens'])
        # Cached input is already part of input_tokens and must not be counted twice.
        limits = dict(result['plan']['limits'])
        for constrained in ({'max_calls': 1}, {'observed_token_stop': 120}, {'max_elapsed_seconds': 1}):
            with self.subTest(limit=constrained):
                self.policy(**{**limits, **constrained})
                with self.assertRaises(ValueError):
                    self.dispatch(worker)
                self.assertEqual(1, len(worker.calls))

    def test_successful_receipt_is_reused_after_queue_restart(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker(usage=False)
        first = self.dispatch(worker)
        self.assertEqual(1, first['plan']['usage']['unknown_usage_runs'])
        self.assertEqual(0, first['plan']['usage']['known_tokens'])
        self.q.db.close()
        self.q = d.Queue(self.root / 'runtime')
        second = self.dispatch(worker)
        self.assertEqual(first['executed'], second['reused'])
        self.assertEqual([], second['executed'])
        self.assertEqual(1, len(worker.calls))

    def test_changed_log_prevents_cached_receipt_reuse(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker()
        first = self.dispatch(worker)
        log = first['runs'][0]['receipt']['result']['logs']['stdout']['path']
        Path(log).write_text('tampered evidence')
        second = self.dispatch(worker)
        self.assertEqual([], second['reused'])
        self.assertEqual(1, len(second['executed']))
        self.assertEqual(2, len(worker.calls))

    def test_empty_stderr_and_noop_patch_reuse_requires_files_to_remain_present(self):
        self.units('left')
        self.start()
        worker = SyntheticWorker(empty_artifacts=True)
        first = self.dispatch(worker)
        second = self.dispatch(worker)
        self.assertEqual(first['executed'], second['reused'])
        self.assertEqual(1, len(worker.calls))
        row = first['runs'][0]['receipt']['result']
        self.assertEqual(b'', Path(row['patch']['path']).read_bytes())
        Path(row['logs']['stderr']['path']).unlink()
        third = self.dispatch(worker)
        self.assertEqual([], third['reused'])
        self.assertEqual(1, len(third['executed']))
        self.assertEqual(2, len(worker.calls))

    def test_candidate_commit_drift_rejects_completed_worker_result(self):
        self.units('left')
        self.start()

        def advance_candidate(_kwargs, _packet):
            (self.repo / 'left/value.txt').write_text('parent advanced candidate\n')
            self.git('add', 'left/value.txt')
            self.git('commit', '-qm', 'concurrent candidate advance')

        result = self.dispatch(SyntheticWorker(hook=advance_candidate))
        self.assertEqual('STALE', result['runs'][0]['status'])
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), result['runs'][0]['receipt']['candidate'])
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', {
                'run_id': result['executed'][0], 'evidence': str(self.evidence)})

    def test_review_revision_drift_rejects_completed_worker_result(self):
        self.units('left')
        self.start()

        def revise_review(_kwargs, _packet):
            queue = d.Queue(self.q.root)
            try:
                bind_all(queue, self.t, 'impl')
            finally:
                queue.db.close()

        result = self.dispatch(SyntheticWorker(hook=revise_review))
        self.assertEqual('STALE', result['runs'][0]['status'])
        self.assertLess(result['runs'][0]['receipt']['review_revision'],
                        self.q.reviews.plan(self.t['id'])['revision'])

    def test_independent_review_needs_controlled_receipt_to_deliver(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.start()
        self.pass_checks()
        forged = review_data(self.q, self.t, self.evidence, d.frozen_commit(self.repo))
        # Whether rejected on record or submit, a free-form reviewer string must
        # never move the task out of RUNNING without a controlled worker receipt.
        try:
            self.q.reviews.record(self.t['id'], 'synthetic-unverified-reviewer', forged)
        except ValueError:
            pass
        with self.assertRaises(ValueError):
            self.q.submit(self.t['id'], 'impl', self.report())
        self.assertEqual('RUNNING', self.q.task(self.t['id'])['status'])
        worker = SyntheticWorker()
        self.dispatch(worker, 'review')
        self.assertEqual([],worker.calls[0]['packet']['review']['allowed_resolved_finding_ids'])
        recorded = self.q.reviews.load(self.t['id'])['review']
        self.assertEqual('codex:synthetic-' + Path(worker.calls[0]['output_dir']).name, recorded['actor'])
        self.assertTrue(recorded['resource_run_id'])
        self.q.submit(self.t['id'], 'impl', self.report())
        self.assertEqual('DELIVERED', self.q.finish(self.t['id'], 'impl')['status'])

    def test_review_receives_complete_source_diff_from_approved_baseline(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.start()
        baseline = self.git('rev-parse', 'HEAD')
        (self.repo / 'left/value.txt').write_text('changed for independent examination\n')
        self.git('add', 'left/value.txt')
        self.git('commit', '-qm', 'synthetic implementation')
        self.pass_checks()
        worker = SyntheticWorker()
        result = self.dispatch(worker, 'review')
        packet = worker.calls[0]['packet']
        self.assertEqual(baseline, packet['review']['baseline'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), packet['candidate'])
        expected = self.git('diff', '--no-ext-diff', '--no-textconv', '--no-color', baseline, 'HEAD', '--')
        self.assertEqual(expected, packet['review']['source_diff'])
        self.assertIn('-left', packet['review']['source_diff'])
        self.assertIn('+changed for independent examination', packet['review']['source_diff'])
        self.assertEqual('SUCCESS', result['runs'][0]['status'])

    def test_review_identity_observation_and_log_tampering_are_rejected(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.start()
        self.pass_checks()
        result = self.dispatch(SyntheticWorker(), 'review')
        recorded = self.q.reviews.load(self.t['id'])['review']
        with self.assertRaises(ValueError):
            self.q.resources.check_review(self.t['id'], 'codex:another-thread', recorded)
        changed = copy.deepcopy(recorded)
        changed['observations'][next(iter(changed['observations']))] = 'rewritten after receipt'
        with self.assertRaises(ValueError):
            self.q.resources.check_review(self.t['id'], recorded['actor'], changed)
        log = result['runs'][0]['receipt']['result']['logs']['stdout']['path']
        Path(log).write_text('changed after independent review')
        with self.assertRaises(ValueError):
            self.q.submit(self.t['id'], 'impl', self.report())
        self.assertEqual('RUNNING', self.q.task(self.t['id'])['status'])

    def test_active_worker_blocks_cancel_delivery_and_duplicate_dispatch(self):
        self.units('left')
        self.start()
        self.pass_checks()
        self.q.reviews.record(self.t['id'], 'impl',
                              review_data(self.q, self.t, self.evidence, d.frozen_commit(self.repo)))
        report = self.report()
        outcomes = []

        def observe_active(_kwargs, _packet):
            queue = d.Queue(self.q.root)
            try:
                self.assertTrue(queue.resources.plan(self.t['id'])['active'])
                for action in (lambda: queue.cancel(self.t['id'], 'synthetic cancellation request'),
                               lambda: queue.submit(self.t['id'], 'impl', report)):
                    try:
                        action()
                    except ValueError:
                        outcomes.append('blocked')
                    else:
                        outcomes.append('incorrectly accepted')
                duplicate = SyntheticWorker()
                rerun = queue.resources.run(self.t['id'], 'impl', 'work', worker=duplicate)
                self.assertEqual([], duplicate.calls)
                self.assertEqual([], rerun['executed'])
                self.assertEqual('RUNNING', queue.task(self.t['id'])['status'])
            finally:
                queue.db.close()

        result = self.dispatch(SyntheticWorker(hook=observe_active))
        self.assertEqual(['blocked', 'blocked'], outcomes)
        self.assertEqual('SUCCESS', result['runs'][0]['status'])
        self.assertEqual([], result['plan']['active'])

    def test_patch_proposal_does_not_change_parent_source_or_commit(self):
        self.units('left')
        self.start()
        head = self.git('rev-parse', 'HEAD')
        result = self.dispatch(SyntheticWorker(emit_patch=True))
        row = result['runs'][0]
        self.assertEqual('SUCCESS', row['status'])
        self.assertTrue(Path(row['receipt']['result']['patch']['path']).is_file())
        self.assertEqual('left\n', (self.repo / 'left/value.txt').read_text())
        self.assertEqual(head, self.git('rev-parse', 'HEAD'))
        self.assertEqual('', self.git('status', '--porcelain'))
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', {
                'run_id': row['id'], 'evidence': str(self.evidence)})

    def test_out_of_scope_patch_is_rejected_and_cannot_be_integrated(self):
        self.units('right')
        self.start()
        result = self.dispatch(SyntheticWorker(emit_patch=True))
        row = result['runs'][0]
        self.assertEqual('SCOPE_VIOLATION', row['status'])
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', {
                'run_id': row['id'], 'evidence': str(self.evidence)})
        self.assertEqual('left\n', (self.repo / 'left/value.txt').read_text())

    def test_component_dependency_waits_for_integrated_source_and_reuses_ancestor(self):
        self.c['review_components'][2]['depends_on'] = ['left']
        self.units('left', 'right')
        self.start()
        self.quota()
        first_worker = SyntheticWorker(emit_patch=True)
        first = self.dispatch(first_worker)
        self.assertEqual(['left-work'], [c['packet']['unit']['id'] for c in first_worker.calls])
        waiting = SyntheticWorker()
        before_integration = self.dispatch(waiting)
        self.assertEqual([], waiting.calls)
        self.assertEqual(first['executed'], before_integration['reused'])
        patch_path = first['runs'][0]['receipt']['result']['patch']['path']
        self.git('apply', patch_path)
        self.git('add', 'left/value.txt')
        self.git('commit', '-qm', 'integrate bounded worker proposal')
        self.q.resources.integrated(self.t['id'], 'impl', {
            'run_id': first['executed'][0], 'evidence': str(self.evidence)})
        after_integration = self.dispatch(waiting)
        self.assertEqual(['right-work'], [c['packet']['unit']['id'] for c in waiting.calls])
        self.assertIn(first['executed'][0], after_integration['reused'])

    def test_successful_noop_integration_unlocks_dependency_without_new_commit(self):
        self.c['review_components'][2]['depends_on'] = ['left']
        self.units('left', 'right')
        self.start()
        self.quota()
        candidate = self.git('rev-parse', 'HEAD')
        first = self.dispatch(SyntheticWorker(empty_artifacts=True))
        rid = first['executed'][0]
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', {
                'run_id': rid, 'evidence': str(self.root / 'absent-evidence')})
        self.q.resources.integrated(self.t['id'], 'impl', {
            'run_id': rid, 'evidence': str(self.evidence)})
        row = self.q.resources.rows(self.t['id'])[0]
        self.assertEqual('INTEGRATED', row['status'])
        self.assertTrue(row['receipt']['integration']['no_change'])
        self.assertEqual('SUCCESS', row['receipt']['integration']['original_status'])
        self.assertEqual(candidate, row['receipt']['integration']['commit'])
        dependent = SyntheticWorker()
        after = self.dispatch(dependent)
        self.assertEqual(['right-work'], [c['packet']['unit']['id'] for c in dependent.calls])
        self.assertIn(rid, after['reused'])
        self.assertEqual(candidate, self.git('rev-parse', 'HEAD'))
        self.assertEqual('', self.git('status', '--porcelain'))

    def test_work_and_review_receipts_reuse_when_quota_and_call_budget_exhausted(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.units('left')
        self.start()
        worker = SyntheticWorker()
        work = self.dispatch(worker)
        self.pass_checks()
        reviewed = self.dispatch(worker, 'review')
        self.assertTrue(self.q.reviews.plan(self.t['id'])['delivery_allowed'])
        self.policy(max_calls=2)
        self.quota(0)
        blocked_plan = self.q.resources.plan(self.t['id'])
        self.assertEqual('EXHAUSTED', blocked_plan['quota']['state'])
        self.assertEqual(blocked_plan['limits']['max_calls'], blocked_plan['usage']['calls'])
        repeated_work = self.dispatch(worker)
        repeated_review = self.dispatch(worker, 'review')
        self.assertEqual(work['executed'], repeated_work['reused'])
        self.assertEqual(reviewed['executed'], repeated_review['reused'])
        self.assertEqual([], repeated_work['executed'])
        self.assertEqual([], repeated_review['executed'])
        self.assertEqual(2, len(worker.calls))
        self.assertEqual(2, len(self.q.resources.rows(self.t['id'])))

    def test_partial_failed_patch_needs_explicit_adoption_and_keeps_failure_history(self):
        self.c['risk_signals'] = ['backend_frontend']
        self.units('left')
        self.start()
        first = self.dispatch(SyntheticWorker(status='BLOCKED', failure_kind='environment',
                                              execution_status='ERROR', emit_patch=True))
        row = first['runs'][0]
        self.assertEqual('BLOCKED', row['status'])
        original_result = copy.deepcopy(row['receipt']['result'])
        receipt_path = Path(row['receipt']['result_file']['path'])
        original_receipt = receipt_path.read_bytes()
        adoption = {'run_id': row['id'], 'evidence': str(self.evidence), 'adopt_partial_patch': True}
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', adoption)
        patch_path = original_result['patch']['path']
        self.git('apply', '--check', patch_path)
        self.git('apply', patch_path)
        self.assertEqual('proposed\n', (self.repo / 'left/value.txt').read_text())
        self.git('add', 'left/value.txt')
        self.git('commit', '-qm', 'inspect and integrate synthetic partial patch')
        self.pass_checks()
        self.diagnose(row['id'], 'resolved')
        with self.assertRaises(ValueError):
            self.q.resources.integrated(self.t['id'], 'impl', {
                'run_id': row['id'], 'evidence': str(self.evidence)})
        self.q.resources.integrated(self.t['id'], 'impl', adoption)
        integrated = self.q.resources.rows(self.t['id'])[0]
        self.assertEqual('INTEGRATED', integrated['status'])
        self.assertEqual('BLOCKED', integrated['receipt']['integration']['original_status'])
        self.assertFalse(integrated['receipt']['integration']['no_change'])
        self.assertEqual(original_result, integrated['receipt']['result'])
        self.assertEqual(original_receipt, receipt_path.read_bytes())
        self.assertFalse(self.q.reviews.plan(self.t['id'])['delivery_allowed'])
        with self.assertRaises(ValueError):
            self.q.submit(self.t['id'], 'impl', self.report())
        self.dispatch(SyntheticWorker(), 'review')
        self.q.submit(self.t['id'], 'impl', self.report())
        self.assertEqual('DELIVERED', self.q.finish(self.t['id'], 'impl')['status'])

    def test_unknown_child_requires_host_attestation_and_dead_runner_before_recovery(self):
        self.units('left')
        self.start()
        # Simulate a launcher crash between a durable reservation and on_start.
        _, reserved, _ = self.q.resources._reserve(self.t['id'], 'impl', 'work')
        rid = reserved[0]['id']
        self.assertIsNone(self.q.resources.rows(self.t['id'])[0]['receipt']['child_pid'])
        with self.assertRaises(ValueError):
            self.q.resources.reconcile(self.t['id'], 'impl', str(self.evidence))
        attestation = self.root / 'host-attestation.json'
        attestation.write_text(json.dumps({'confirmed_stopped_run_ids': [rid],
                                           'process_scan_evidence': str(self.evidence)}))
        with self.assertRaises(ValueError):
            self.q.resources.reconcile(self.t['id'], 'impl', str(attestation))
        self.assertEqual('RESERVED', self.q.resources.rows(self.t['id'])[0]['status'])
        with patch.object(engine.os, 'kill', side_effect=ProcessLookupError) as process_check:
            result = self.q.resources.reconcile(self.t['id'], 'impl', str(attestation))
        process_check.assert_called_once_with(os.getpid(), 0)
        self.assertEqual([], result['active'])
        self.assertEqual('INTERRUPTED', self.q.resources.rows(self.t['id'])[0]['status'])
        with self.assertRaises(ValueError):
            self.dispatch(SyntheticWorker())
        self.diagnose(rid, 'resolved')
        self.assertEqual(1, len(self.dispatch(SyntheticWorker())['executed']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
