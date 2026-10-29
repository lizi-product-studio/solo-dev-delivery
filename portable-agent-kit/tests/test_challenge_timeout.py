"""Bounded, real disposable-process timeout checks; no Agent processes involved."""
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

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import challenge


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
        return not (stat.exists() and stat.read_text(encoding='ascii').split()[2] == 'Z')
    except (ProcessLookupError, FileNotFoundError):
        return False


def wait_until_gone(pid):
    deadline = time.monotonic() + 2
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not alive(pid)


class ChallengeTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='solo-timeout-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'candidate'
        self.work.mkdir()
        (self.work / 'goal.md').write_text('Return one.\n', encoding='utf-8')
        (self.work / 'candidate.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.packet, self.test, self.receipt = [self.root / p for p in ('packet.json', 'checks.py', 'receipt.json')]
        self.pids = self.root / 'test-pids.json'
        challenge.freeze(self.work, ['goal.md'], ['candidate.py'], self.packet)
        self.addCleanup(self.cleanup_test_pids)

    def cleanup_test_pids(self):
        if not self.pids.exists():
            return
        for pid in reversed(json.loads(self.pids.read_text(encoding='utf-8'))):
            if alive(pid):
                if os.name == 'nt':
                    subprocess.run([str(Path(os.environ['SYSTEMROOT']) / 'System32/taskkill.exe'), '/PID', str(pid), '/T', '/F'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                else:
                    os.kill(pid, signal.SIGKILL)
                self.assertTrue(wait_until_gone(pid), 'test cleanup could not stop owned process: ' + str(pid))

    def write_test(self, body):
        self.test.write_text('import os, sys, time, json, subprocess, unittest\nfrom pathlib import Path\n'
                             'import candidate\nclass Business(unittest.TestCase):\n def test_real(self):\n' + body, encoding='utf-8')

    def test_actual_timeout_kills_only_its_test_tree_and_persists_unknown(self):
        self.write_test('  child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8)"])\n'
                        '  Path(' + repr(str(self.pids)) + ').write_text(json.dumps([os.getpid(), child.pid]), encoding="utf-8")\n'
                        '  time.sleep(8)\n')
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
        try:
            started = time.monotonic()
            result = challenge.run(self.packet, self.test, self.receipt, 1)
            self.assertLess(time.monotonic() - started, 7)
            self.assertEqual((result['status'], result['timed_out']), ('UNKNOWN', True))
            self.assertEqual(result['process_cleanup']['status'], 'COMPLETE',
                             json.dumps(result['process_cleanup'], ensure_ascii=False, sort_keys=True))
            self.assertEqual(challenge.status(self.receipt)['status'], 'UNKNOWN')
            self.assertTrue(self.pids.exists(), 'real test and child must have started')
            for pid in json.loads(self.pids.read_text(encoding='utf-8')):
                self.assertTrue(wait_until_gone(pid), 'timed-out test process still running: ' + str(pid))
            self.assertIsNone(unrelated.poll(), 'must not kill an unrelated Python process')
        finally:
            unrelated.kill()
            unrelated.wait(timeout=5)

    def test_cli_timeout_is_nonpassing_but_receipt_can_be_read(self):
        self.write_test('  time.sleep(2)\n')
        result = subprocess.run([sys.executable, '-B', str(KIT / 'scripts/challenge.py'), 'run', '--packet', str(self.packet),
                                 '--test', str(self.test), '--receipt', str(self.receipt), '--timeout', '1'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
        self.assertEqual(result.returncode, 1, result.stderr)
        value = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual((value['status'], value['timed_out']), ('UNKNOWN', True))
        self.assertEqual(challenge.status(self.receipt)['status'], 'UNKNOWN')

    def test_normal_pass_and_failure_keep_evidence_and_do_not_run_termination(self):
        for expected, status in ((1, 'CHALLENGE_PASS'), (2, 'FAIL')):
            with self.subTest(expected=expected):
                self.write_test('  self.assertEqual(candidate.VALUE, ' + str(expected) + ')\n')
                receipt = self.root / (status + '.json')
                with patch.object(challenge, '_terminate_test_tree', side_effect=AssertionError('normal execution must not terminate a tree')):
                    value = challenge.run(self.packet, self.test, receipt, 3)
                self.assertEqual((value['status'], value['timed_out']), (status, False))
                self.assertEqual(challenge.status(receipt)['status'], status)
                (self.work / 'candidate.py').write_text('VALUE = 99\n', encoding='utf-8')
                self.assertEqual(challenge.status(receipt)['status'], 'STALE')
                (self.work / 'candidate.py').write_text('VALUE = 1\n', encoding='utf-8')

    def test_platform_termination_failure_is_recorded_in_unknown_receipt(self):
        self.write_test('  time.sleep(2)\n')
        target = 'subprocess.run' if os.name == 'nt' else 'os.killpg'
        with patch('challenge.' + target, side_effect=OSError('injected platform cleanup failure')):
            value = challenge.run(self.packet, self.test, self.receipt, 1)
        self.assertEqual((value['status'], value['timed_out']), ('UNKNOWN', True))
        self.assertEqual(value['process_cleanup']['status'], 'FAILED')
        self.assertIn('injected platform cleanup failure', json.dumps(value['process_cleanup']))
        self.assertEqual(challenge.status(self.receipt)['status'], 'UNKNOWN')

    def test_posix_branch_targets_only_the_new_test_process_group(self):
        process = Mock(pid=12345)
        process.wait.return_value = -9
        with patch.object(challenge.os, 'name', 'posix'), patch.object(challenge.os, 'killpg', create=True) as killpg, \
                patch.object(challenge.signal, 'SIGKILL', 9, create=True):
            result = challenge._terminate_test_tree(process)
        killpg.assert_called_once_with(process.pid, 9)
        process.wait.assert_called_once_with(timeout=5)
        process.kill.assert_not_called()
        self.assertEqual(result['status'], 'TERMINATED')

    def test_temporary_cleanup_failure_cannot_turn_a_pass_into_success(self):
        self.write_test('  self.assertEqual(candidate.VALUE, 1)\n')
        cleanup = tempfile.TemporaryDirectory.cleanup

        def fail_after_cleanup(directory):
            cleanup(directory)
            raise OSError('injected scratch cleanup failure')

        with patch.object(tempfile.TemporaryDirectory, 'cleanup', fail_after_cleanup):
            value = challenge.run(self.packet, self.test, self.receipt, 3)
        self.assertEqual(value['status'], 'UNKNOWN')
        self.assertEqual(value['process_cleanup']['status'], 'FAILED')
        self.assertIn('injected scratch cleanup failure', json.dumps(value['process_cleanup']))
        self.assertEqual(challenge.status(self.receipt)['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
