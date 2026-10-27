"""Real owned worker trees and persisted handoff receipts; no model or account."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import codex_worker as worker


def alive(pid):
    if os.name == 'nt':
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() != 87:
                raise ctypes.WinError(ctypes.get_last_error())
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path('/proc') / str(pid) / 'stat'
        return not (stat.exists() and stat.read_text().split()[2] == 'Z')
    except (ProcessLookupError, FileNotFoundError):
        return False


FAKE = '''import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
output = Path(args[args.index('-o')+1])
instructions = sys.stdin.read()
mode = instructions.split('FIXTURE:',1)[1].splitlines()[0]
print(json.dumps({'type':'thread.started','thread_id':'local-worker-fixture'}),flush=True)
if mode not in ('normal', 'slow'):
    Path('app.txt').write_text('preserved partial work\\n')
    child = subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    (output.parent/'pids.json').write_text(json.dumps([os.getpid(),child.pid]))
    time.sleep(30)
if mode == 'slow': time.sleep(0.25)
answer={'status':'PASS','summary':'Local fixture only','failure_kind':'none',
        'observations':[],'findings':[],'resolved_findings':[],'uncovered':[]}
output.write_text(json.dumps(answer))
print(json.dumps({'type':'turn.completed'}),flush=True)
'''


class WorkerStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='worker-stop-', dir=os.environ.get('SOLO_TEST_ARTIFACT_ROOT'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owned = []
        self.addCleanup(self.cleanup_owned)

    def cleanup_owned(self):
        for pid in reversed(self.owned):
            if not alive(pid):
                continue
            if os.name == 'nt':
                subprocess.run([str(Path(os.environ['SYSTEMROOT'])/'System32'/'taskkill.exe'),
                                '/PID',str(pid),'/T','/F'],capture_output=True,timeout=5)
            else:
                os.kill(pid, signal.SIGKILL)
            deadline=time.monotonic()+2
            while alive(pid) and time.monotonic()<deadline: time.sleep(0.02)
            self.assertFalse(alive(pid),'owned fixture survived cleanup: '+str(pid))

    def setup_repository(self):
        self.repo = self.root/'repository'; self.repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.PIPE).decode().strip()
        git('init','--template=')
        git('config','user.name','Local fixture');git('config','user.email','fixture@example.invalid')
        (self.repo/'app.txt').write_text('baseline\n')
        git('add','.');git('commit','-qm','local baseline')
        self.head=git('rev-parse','HEAD')
        script=self.root/'fixture.py';script.write_text(FAKE,encoding='utf-8')
        if os.name == 'nt':
            self.executable=self.root/'fixture.cmd'
            self.executable.write_text('@echo off\n"'+sys.executable+'" "%~dp0fixture.py" %*\n')
        else:
            self.executable=self.root/'fixture'
            self.executable.write_text('#!'+sys.executable+'\n'+FAKE)
            self.executable.chmod(0o755)

    def run_worker(self, mode='handoff', **kwargs):
        def takeover(progress):
            pids=Path(progress['workspace']).parent/'pids.json'
            if pids.exists():
                self.owned.extend(json.loads(pids.read_text()))
                return {'action':'takeover','sequence':1,'reason':'Explicit fixture parent takeover'}
        options=dict(executable=self.executable,repository=self.repo,candidate=self.head,
                     role='implement',model='local-fixture',effort='low',prompt='FIXTURE:'+mode,
                     output_dir=self.root/'runs',timeout=0.05,observation_seconds=0.05,
                     on_start=self.owned.append,supervise=takeover)
        options.update(kwargs)
        result=worker.run_worker(**options)
        saved=json.loads((Path(result['output_dir'])/'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(result,saved)
        return result

    @unittest.skipUnless(os.name == 'nt', 'real Windows process tree')
    def test_windows_stops_owned_parent_and_child_but_not_unrelated_process(self):
        pidfile=self.root/'child.json'
        code=('import json,os,subprocess,sys,time;from pathlib import Path;'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
              'Path(sys.argv[1]).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(30)')
        process=subprocess.Popen([sys.executable,'-c',code,str(pidfile)])
        self.owned.append(process.pid)
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            deadline=time.monotonic()+5
            while not pidfile.exists() and time.monotonic()<deadline: time.sleep(0.02)
            pids=json.loads(pidfile.read_text()); self.owned.extend(pids)
            result=worker._stop(process)
            self.assertEqual(('COMPLETE',True,True),(result['status'],result['leader_stopped'],result['tree_stopped']))
            self.assertTrue(all(not alive(pid) for pid in pids))
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill();unrelated.wait(timeout=5)

    def test_real_takeover_persists_receipt_and_preserves_stopped_patch(self):
        self.setup_repository()
        result=self.run_worker()
        self.assertEqual('PARENT_HANDOFF',result['execution_status'],result)
        self.assertEqual('takeover',result['parent_decision']['action'])
        self.assertEqual('COMPLETE',result['process_cleanup']['status'])
        self.assertIs(result['process_running'],False)
        self.assertTrue(all(not alive(pid) for pid in set(self.owned)))
        self.assertIn(b'+preserved partial work',Path(result['patch']['path']).read_bytes())
        progress=json.loads(Path(result['supervision']['progress_file']).read_text(encoding='utf-8'))
        self.assertFalse(progress['process_running']);self.assertFalse(progress['needs_decision'])
        self.assertEqual('baseline\n',(self.repo/'app.txt').read_text())

    def test_termination_failure_persists_visible_block_and_no_live_patch(self):
        self.setup_repository()
        actual_run=subprocess.run
        def failed_taskkill(argv,*args,**kwargs):
            if Path(argv[0]).name.lower()=='taskkill.exe': raise OSError('injected stop failure')
            return actual_run(argv,*args,**kwargs)
        inject=patch('codex_worker.subprocess.run',side_effect=failed_taskkill) if os.name=='nt' else patch('codex_worker.os.killpg',side_effect=OSError('injected stop failure'))
        with inject:
            result=self.run_worker()
        self.assertEqual(('BLOCKED','ERROR','FAILED'),(result['status'],result['execution_status'],result['process_cleanup']['status']))
        self.assertIsNot(result['process_running'],False)
        self.assertIsNone(result['patch'])
        self.assertIn('injected stop failure',result['summary'])
        self.assertEqual('takeover',result['parent_decision']['action'])
        progress=json.loads(Path(result['supervision']['progress_file']).read_text(encoding='utf-8'))
        self.assertIsNot(progress['process_running'],False);self.assertTrue(progress['needs_decision'])
        self.assertIn('PROCESS_STOP_UNCONFIRMED',progress['decision_reasons'])

    def test_unexpected_stop_exception_still_persists_final_receipt(self):
        self.setup_repository()
        with patch.object(worker,'_stop',side_effect=RuntimeError('unexpected cleanup fault')):
            result=self.run_worker()
        self.assertEqual('FAILED',result['process_cleanup']['status'])
        self.assertIsNone(result['patch']);self.assertIsNot(result['process_running'],False)
        self.assertIn('unexpected cleanup fault',result['summary'])

    @unittest.skipUnless(os.name == 'nt', 'real Windows leader-only stop')
    def test_dead_leader_with_unconfirmed_tree_stays_unknown_and_has_no_patch(self):
        self.setup_repository()
        actual_run=subprocess.run
        def stop_leader_only(argv,*args,**kwargs):
            if Path(argv[0]).name.lower()=='taskkill.exe':
                stopped=actual_run([arg for arg in argv if arg!='/T'],*args,**kwargs)
                return subprocess.CompletedProcess(argv,1,stopped.stdout,b'fixture tree stop not confirmed')
            return actual_run(argv,*args,**kwargs)
        with patch('codex_worker.subprocess.run',side_effect=stop_leader_only):
            result=self.run_worker()
        self.assertTrue(result['process_cleanup']['leader_stopped'])
        self.assertFalse(result['process_cleanup']['tree_stopped'])
        self.assertIsNone(result['process_running']);self.assertIsNone(result['patch'])
        self.assertTrue(any(alive(pid) for pid in self.owned))
        progress=json.loads(Path(result['supervision']['progress_file']).read_text(encoding='utf-8'))
        self.assertIsNone(progress['process_running']);self.assertTrue(progress['needs_decision'])

    def test_on_start_error_stops_worker_and_retains_original_diagnostic(self):
        self.setup_repository()
        def failed_start(pid):
            self.owned.append(pid)
            raise OSError('host could not persist launched identity')
        result=self.run_worker(on_start=failed_start)
        self.assertEqual(('BLOCKED','ERROR'),(result['status'],result['execution_status']))
        self.assertIn('host could not persist',result['summary'])
        self.assertEqual('COMPLETE',result['process_cleanup']['status'])
        self.assertIs(result['process_running'],False)

    @unittest.skipUnless(os.name == 'nt', 'Windows exited leader cannot prove tree stop')
    def test_start_exception_after_leader_exit_keeps_stop_unknown(self):
        self.setup_repository()
        self.executable.write_text('@exit /b 2\n')
        def failed_start(pid):
            self.owned.append(pid)
            deadline=time.monotonic()+5
            while alive(pid) and time.monotonic()<deadline: time.sleep(0.02)
            self.assertFalse(alive(pid))
            raise OSError('host failed after leader exited')
        result=self.run_worker(on_start=failed_start)
        self.assertEqual(('BLOCKED','FAILED'),(result['status'],result['process_cleanup']['status']))
        self.assertIsNone(result['process_running']);self.assertIsNone(result['patch'])

    def test_normal_completion_does_not_attempt_tree_stop(self):
        self.setup_repository()
        for mode in ('normal','slow'):
            with self.subTest(mode=mode),patch.object(worker,'_stop',side_effect=AssertionError('normal completion must not stop')):
                result=self.run_worker(mode,supervise=None)
            self.assertEqual(('PASS','COMPLETED'),(result['status'],result['execution_status']))
            self.assertIs(result['process_running'],False)
            self.assertNotIn('process_cleanup',result)
            if mode == 'slow': self.assertGreater(result['seconds'],0.25)

    def test_posix_retains_term_then_kill_group_and_bounded_wait(self):
        process=Mock(pid=12345);process.poll.return_value=-9
        with patch.object(worker.os,'name','posix'),patch.object(worker.os,'killpg',create=True) as killpg,patch.object(worker.signal,'SIGKILL',9,create=True):
            result=worker._stop(process)
        self.assertEqual([(12345,signal.SIGTERM),(12345,9)],[c.args for c in killpg.call_args_list])
        process.communicate.assert_called_once_with(timeout=2);process.wait.assert_called_once_with(timeout=5)
        self.assertEqual('COMPLETE',result['status'])


if __name__=='__main__': unittest.main()
