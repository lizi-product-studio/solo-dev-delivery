"""Real Git/process tests using an explicit Python CLI test double; no model calls or fees."""
import json
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import codex_worker as worker


FAKE_CLI = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
output = Path(args[args.index('-o') + 1])
request = sys.stdin.read()
mode = request.rsplit('SCENARIO:', 1)[-1].splitlines()[0].strip()
root = Path.cwd()
capture = {'argv': args, 'env_keys': sorted(os.environ), 'prompt': request,
           'source': (root/'app.txt').read_text(), 'paths': sorted(str(p.relative_to(root)) for p in root.rglob('*')),
           'git_config': (root/'.git/config').read_text()}
(output.parent/'capture.json').write_text(json.dumps(capture))
print(json.dumps({'type':'thread.started','thread_id':'test-double-thread'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
answer = {'status':'PASS','summary':'Synthetic CLI fixture only','failure_kind':'none',
          'observations':[{'check_id':'A','observed':'Synthetic observation'}],
          'findings':[],'resolved_findings':[],'uncovered':[]}
if mode in ('productive', 'quiet', 'budget'):
    if mode == 'quiet':
        print(json.dumps({'type':'item.started','item':{'id':'long-test','type':'command_execution','status':'in_progress'}}), flush=True)
        time.sleep(0.65)
        print(json.dumps({'type':'item.completed','item':{'id':'long-test','type':'command_execution','status':'completed','exit_code':0}}), flush=True)
    else:
        for i in range(20 if mode == 'budget' else 5):
            (root/'app.txt').write_text('progress '+str(i))
            print(json.dumps({'type':'item.completed','item':{'id':str(i),'type':'file_change','status':'completed'}}), flush=True)
            time.sleep(0.1)
if mode == 'timeout':
    code = 'import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(3);Path("survived.txt").write_text("bad");time.sleep(30)'
    child = subprocess.Popen([sys.executable, '-c', code])
    (output.parent/'descendant.pid').write_text(str(child.pid))
    time.sleep(30)
if mode in ('mutate', 'commit', 'unverified_patch'):
    (root/'app.txt').write_text('implemented\n')
    (root/'delete.txt').unlink()
    (root/'ignored.txt').write_text('must be captured\n')
    (root/'new.bin').write_bytes(b'\x00\x01\x02')
    if mode == 'commit':
        subprocess.run(['git', 'add', '-Af', '.'], check=True)
        subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','agent commit'], check=True)
if mode == 'metadata':
    (root/'.git/worker-extra').write_text('mutation')
if mode == 'malformed_jsonl':
    print('not json')
if mode == 'error':
    print(json.dumps({'type':'error','message':'Synthetic error'}))
if mode == 'duplicate_thread':
    print(json.dumps({'type':'thread.started','thread_id':'second'}))
if mode == 'extra_field':
    answer['unexpected'] = True
if mode == 'nested_extra':
    answer['observations'][0]['unexpected'] = True
if mode in ('findings', 'pass_findings'):
    answer.update(status='FINDINGS', findings=[{'severity':'P2','family':'data_integrity','components':['main'],
                  'description':'Synthetic finding','reproduction':'Synthetic reproduction'}])
if mode == 'pass_findings':
    answer['status'] = 'PASS'
if mode in ('contradictory', 'unverified_patch'):
    answer['uncovered'] = ['A']
if mode in ('empty_findings', 'incomplete_semantic', 'nonzero_semantic'):
    answer['status'] = 'FINDINGS'
if mode == 'blocked_none':
    answer['status'] = 'BLOCKED'
if mode == 'pass_failure':
    answer['failure_kind'] = 'reasoning'
if mode != 'missing_output':
    output.write_text(json.dumps(answer))
event = {'type':'turn.completed','usage':{'input_tokens':12,'cached_input_tokens':3,'output_tokens':4}}
if mode == 'no_usage':
    event.pop('usage')
if mode == 'bad_usage':
    event['usage']['input_tokens'] = True
if mode not in ('incomplete', 'incomplete_semantic'):
    print(json.dumps(event), flush=True)
if mode in ('nonzero', 'nonzero_semantic'):
    sys.exit(2)
'''


class CodexWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repository'
        self.repo.mkdir()
        self.git('init', '--template=')
        self.git('config', 'user.name', 'Synthetic fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        for name, content in {'app.txt':'committed\n','delete.txt':'delete me\n', '.gitignore':'ignored.txt\n',
                              '.gitattributes':'exported.txt export-ignore\n', 'exported.txt':'retained\n'}.items():
            (self.repo / name).write_text(content)
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture baseline')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.fake = self.root / 'synthetic-codex'
        self.fake.write_text('#!' + sys.executable + '\n' + FAKE_CLI)
        self.fake.chmod(0o755)
        self.pids = []

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE).decode()

    def run_worker(self, mode='pass', role='review', **overrides):
        kwargs = dict(executable=str(self.fake), repository=self.repo, candidate=self.head, role=role,
                      model='synthetic-test-model', effort='low', prompt='SCENARIO:' + mode,
                      output_dir=self.root / 'runs', timeout=5, on_start=self.pids.append)
        kwargs.update(overrides)
        return worker.run_worker(**kwargs)

    def test_launch_guard_spans_real_process_start_and_pid_receipt(self):
        sequence=[]
        @contextlib.contextmanager
        def guard():
            sequence.append('guard-enter')
            yield
            sequence.append('guard-exit')
        def started(pid):
            self.assertEqual(['guard-enter'],sequence)
            sequence.append('pid-recorded');self.pids.append(pid)
        result=self.run_worker(launch_guard=guard,on_start=started)
        self.assertEqual('COMPLETED',result['execution_status'])
        self.assertEqual(['guard-enter','pid-recorded','guard-exit'],sequence)

    def test_rejected_launch_guard_never_starts_process(self):
        @contextlib.contextmanager
        def guard():
            raise ValueError('Synthetic policy revoked before launch')
            yield
        result=self.run_worker(launch_guard=guard)
        self.assertEqual('ERROR',result['execution_status'])
        self.assertIsNone(result['child_pid']);self.assertFalse(self.pids)

    def test_committed_snapshot_is_independent_and_uses_safe_cli_configuration(self):
        (self.repo / 'app.txt').write_text('uncommitted secret')
        (self.repo / 'local-only.txt').write_text('untracked secret')
        self.git('remote', 'add', 'origin', 'https://example.invalid/private.git')
        hooks = self.repo / '.git/hooks'
        hooks.mkdir()
        (hooks / 'pre-commit').write_text('must not be inherited')
        with patch.dict(os.environ, {'OPENAI_API_KEY':'synthetic-secret', 'CODEX_API_KEY':'synthetic-secret'}):
            result = self.run_worker()
        self.assertEqual('PASS', result['status'], result)
        self.assertEqual('COMPLETED', result['execution_status'])
        self.assertEqual(self.head, result['candidate'])
        self.assertEqual(self.head, result['snapshot_commit'])
        self.assertEqual([result['child_pid']], self.pids)
        capture = json.loads((Path(result['output_dir']) / 'capture.json').read_text())
        self.assertEqual('committed\n', capture['source'])
        self.assertIn('exported.txt', capture['paths'])
        self.assertNotIn('local-only.txt', capture['paths'])
        self.assertNotIn('.git/hooks/pre-commit', capture['paths'])
        self.assertNotIn('remote', capture['git_config'])
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
            self.assertNotIn(key, capture['env_keys'])
        for flag in ('--json', '--ephemeral', '--ignore-user-config', 'agents.enabled=false',
                     'forced_login_method="chatgpt"', 'approval_policy="never"', 'read-only'):
            self.assertIn(flag, capture['argv'])
        self.assertNotIn('--ignore-rules', capture['argv'])
        self.assertEqual('-', capture['argv'][-1])
        self.assertNotIn('SCENARIO:pass', capture['argv'])
        self.assertEqual('uncommitted secret', (self.repo / 'app.txt').read_text())
        self.assertEqual({'input_tokens':12,'cached_input_tokens':3,'output_tokens':4}, result['usage'])
        for proof in result['logs'].values():
            self.assertEqual(proof['sha256'], worker.hashlib.sha256(Path(proof['path']).read_bytes()).hexdigest())

    def test_snapshot_preserves_candidate_commit_and_tree_without_ancestor_history(self):
        parent = self.head
        (self.repo / 'app.txt').write_text('candidate source\n')
        self.git('add', 'app.txt')
        self.git('commit', '-qm', 'candidate with an existing parent')
        self.head = self.git('rev-parse', 'HEAD').strip()
        result = self.run_worker()
        self.assertEqual('PASS', result['status'], result)
        workspace = Path(result['workspace'])
        self.assertEqual(self.head, worker._git(workspace, 'rev-parse', 'HEAD').decode().strip())
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}').strip(),
                         worker._git(workspace, 'write-tree').decode().strip())
        self.assertEqual(worker._git(self.repo, 'cat-file', 'commit', self.head),
                         worker._git(workspace, 'cat-file', 'commit', self.head))
        self.assertEqual(self.head, worker._git(workspace, 'rev-list', 'HEAD').decode().strip())
        self.assertEqual(self.head + '\n', (workspace / '.git/shallow').read_text())
        self.assertFalse((workspace / '.git/objects/info/alternates').exists())
        with self.assertRaises(subprocess.CalledProcessError):
            worker._git(workspace, 'cat-file', '-e', parent)

    def test_patch_includes_new_deleted_binary_and_ignored_files_after_agent_commit(self):
        result = self.run_worker('commit', role='implement')
        self.assertEqual('PASS', result['status'], result)
        patch_file = Path(result['patch']['path'])
        data = patch_file.read_bytes()
        for expected in (b'deleted file mode', b'new file mode', b'ignored.txt', b'GIT binary patch', b'+implemented'):
            self.assertIn(expected, data)
        self.git('apply', '--check', str(patch_file))
        self.assertEqual(self.head, self.git('rev-parse', 'HEAD').strip())
        self.assertEqual('committed\n', (self.repo / 'app.txt').read_text())
        self.assertEqual(['app.txt', 'delete.txt', 'ignored.txt', 'new.bin'], result['changed_paths'])
        self.assertEqual(self.head, result['snapshot_commit'])
        self.assertNotEqual(self.head, worker._git(result['workspace'], 'rev-parse', 'HEAD').decode().strip())
        self.assertEqual(self.head, worker._git(result['workspace'], 'rev-parse', 'HEAD^').decode().strip())
        capture = json.loads((Path(result['output_dir']) / 'capture.json').read_text())
        self.assertIn('workspace-write', capture['argv'])

    def test_read_only_roles_reject_source_and_git_metadata_mutations(self):
        for role in ('review', 'explore'):
            for mode in ('mutate', 'commit', 'metadata'):
                with self.subTest(role=role, mode=mode):
                    result = self.run_worker(mode, role=role)
                    self.assertEqual('BLOCKED', result['status'])
                    self.assertIn('mutated', result['summary'])
                    self.assertIsNone(result['patch'])

    def test_protocol_and_output_faults_cannot_report_success(self):
        for mode in ('malformed_jsonl', 'error', 'duplicate_thread', 'incomplete', 'bad_usage', 'nonzero',
                     'extra_field', 'nested_extra', 'missing_output', 'incomplete_semantic', 'nonzero_semantic'):
            with self.subTest(mode=mode):
                result = self.run_worker(mode)
                self.assertEqual('BLOCKED', result['status'], result)
                self.assertEqual('ERROR', result['execution_status'])
                self.assertEqual('environment', result['failure_kind'])
                self.assertIn('stdout', result['logs'])

    def test_implementation_pass_hands_off_patch_with_uncovered_checks(self):
        result = self.run_worker('unverified_patch', role='implement')
        self.assertEqual('PASS', result['status'], result)
        self.assertEqual('COMPLETED', result['execution_status'])
        self.assertEqual('none', result['failure_kind'])
        self.assertEqual(['A'], result['uncovered'])
        self.assertIn(b'+implemented', Path(result['patch']['path']).read_bytes())
        self.assertEqual('committed\n', (self.repo / 'app.txt').read_text())

    def test_read_only_pass_with_uncovered_is_completed_reasoning_failure(self):
        for role in ('review', 'explore'):
            with self.subTest(role=role):
                result = self.run_worker('contradictory', role=role)
                self.assertEqual('BLOCKED', result['status'])
                self.assertEqual('reasoning', result['failure_kind'])
                self.assertEqual('COMPLETED', result['execution_status'])
                self.assertEqual(['A'], result['uncovered'])
                raw_answer = json.loads(Path(result['logs']['result']['path']).read_text())
                self.assertEqual('PASS', raw_answer['status'])
                self.assertEqual(['A'], raw_answer['uncovered'])

    def test_semantic_contradictions_are_reasoning_failures_only_after_verified_completion(self):
        for mode in ('empty_findings', 'blocked_none', 'pass_failure', 'pass_findings'):
            for role in ('implement', 'review', 'explore'):
                with self.subTest(mode=mode, role=role):
                    result = self.run_worker(mode, role=role)
                    self.assertEqual('BLOCKED', result['status'], result)
                    self.assertEqual('reasoning', result['failure_kind'])
                    self.assertEqual('COMPLETED', result['execution_status'])
                    self.assertEqual(0, result['returncode'])
                    self.assertEqual('test-double-thread', result['thread_id'])
                    self.assertEqual(12, result['usage']['input_tokens'])

    def test_unavailable_usage_is_null_and_findings_are_preserved(self):
        self.assertIsNone(self.run_worker('no_usage')['usage'])
        result = self.run_worker('findings')
        self.assertEqual('FINDINGS', result['status'])
        self.assertEqual('data_integrity', result['findings'][0]['family'])

    def test_productive_worker_survives_multiple_observation_intervals(self):
        result=self.run_worker('productive',role='implement',timeout=3,observation_seconds=0.15)
        self.assertEqual('COMPLETED',result['execution_status'])
        self.assertEqual('PASS',result['status'])
        self.assertIn('app.txt',result['changed_paths'])
        progress=json.loads(Path(result['supervision']['progress_file']).read_text())
        self.assertGreaterEqual(progress['completed_items'],5)
        self.assertEqual('COMPLETED',progress['state'])

    def test_quiet_running_tool_is_not_automatically_killed(self):
        result=self.run_worker('quiet',timeout=3,observation_seconds=0.1)
        self.assertEqual('COMPLETED',result['execution_status'])
        self.assertEqual('PASS',result['status'])

    def test_elapsed_budget_without_parent_response_keeps_work_running(self):
        observations=[]
        result=self.run_worker('budget',role='implement',timeout=0.15,observation_seconds=0.1,
                              supervise=lambda p: observations.append(dict(p)))
        self.assertEqual('COMPLETED',result['execution_status'])
        self.assertEqual('PASS',result['status'])
        self.assertTrue(any(p['needs_decision'] and p['elapsed_seconds']>0.5 for p in observations))
        self.assertGreater(result['seconds'],1.5)

    def test_parent_continue_keeps_same_process_and_applies_checkpoint(self):
        seen=[]
        def control(progress):
            seen.append(dict(progress))
            if progress['needs_decision'] and progress['decision_sequence']==0:
                return {'sequence':1,'action':'continue','next_checkpoint_seconds':3,
                        'reason':'Synthetic parent inspected ongoing bounded work'}
        result=self.run_worker('budget',role='implement',timeout=0.15,observation_seconds=0.3,supervise=control)
        self.assertEqual('COMPLETED',result['execution_status'])
        self.assertEqual(1,len({p['child_pid'] for p in seen}))
        self.assertTrue(any(p['decision_sequence']==1 for p in seen))
        self.assertEqual([result['child_pid']],self.pids)

    def test_control_channel_failure_is_not_stop_authority(self):
        def unavailable(progress):raise OSError('Synthetic temporary control outage')
        result=self.run_worker('productive',role='implement',timeout=0.1,observation_seconds=0.1,supervise=unavailable)
        self.assertEqual('COMPLETED',result['execution_status'])

    def test_parent_takeover_terminates_group_including_child_ignoring_term(self):
        def takeover(progress):
            if progress['elapsed_seconds']>=0.5:
                return {'sequence':1,'action':'takeover','reason':'Synthetic parent explicitly takes over'}
        result = self.run_worker('timeout', timeout=0.1,observation_seconds=0.1,supervise=takeover)
        self.assertEqual('PARENT_HANDOFF', result['execution_status'], result)
        self.assertEqual('takeover',result['parent_decision']['action'])
        self.assertEqual('BLOCKED', result['status'])
        self.assertIsNone(result['usage'])
        self.assertIsNotNone(result['returncode'])
        self.assertTrue((Path(result['output_dir']) / 'descendant.pid').exists())
        time.sleep(2.2)
        self.assertFalse((Path(result['workspace']) / 'survived.txt').exists())

    def test_parent_adjust_preserves_partial_patch(self):
        def adjust(progress):
            if progress['elapsed_seconds']>=0.35:
                return {'sequence':1,'action':'adjust','reason':'Synthetic parent changes next unit',
                        'adjustment':'Review preserved patch before a new dispatch'}
        result=self.run_worker('budget',role='implement',timeout=0.1,observation_seconds=0.1,supervise=adjust)
        self.assertEqual('PARENT_HANDOFF',result['execution_status'])
        self.assertEqual('adjust',result['parent_decision']['action'])
        self.assertIn('app.txt',result['changed_paths'])
        self.assertTrue(Path(result['patch']['path']).stat().st_size>0)
        terminal=json.loads(Path(result['supervision']['progress_file']).read_text())
        self.assertFalse(terminal['process_running'])
        self.assertFalse(terminal['needs_decision'])

    def test_snapshot_rejects_external_symlink_before_model_launch(self):
        (self.repo / 'escape').symlink_to('../outside')
        self.git('add', 'escape')
        self.git('commit', '-qm', 'external symlink')
        result = self.run_worker(candidate=self.git('rev-parse', 'HEAD').strip())
        self.assertEqual('BLOCKED', result['status'])
        self.assertIn('symlink', result['summary'])
        self.assertEqual([], self.pids)

    def test_missing_executable_and_stale_candidate_are_explicit_failures(self):
        result = self.run_worker(executable=str(self.root / 'absent-cli'))
        self.assertEqual('BLOCKED', result['status'])
        self.assertIsNone(result['usage'])
        self.git('commit', '--allow-empty', '-qm', 'new head')
        result = self.run_worker()
        self.assertEqual('BLOCKED', result['status'])
        self.assertIn('current committed HEAD', result['summary'])
        self.assertEqual([], self.pids)

    def test_failed_start_callback_stops_the_launched_worker(self):
        def abort(pid):
            self.pids.append(pid)
            raise RuntimeError('Synthetic host could not persist child identity')
        result = self.run_worker(on_start=abort)
        self.assertEqual('BLOCKED', result['status'])
        self.assertIn('could not persist', result['summary'])
        self.assertEqual([result['child_pid']], self.pids)
        # Windows raises OSError(WinError 87) for os.kill(pid, 0) after the
        # process is gone; POSIX raises ProcessLookupError.
        with self.assertRaises((ProcessLookupError, OSError)):
            os.kill(result['child_pid'], 0)


if __name__ == '__main__':
    unittest.main()
