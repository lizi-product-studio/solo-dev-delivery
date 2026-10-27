"""Installed hook expressions exercised by the actual host shells; no model."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import gate


class HookInstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='solo-hook-command-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'host'

    def test_legacy_handler_upgrades_in_place_preserving_other_fields(self):
        command = 'python3 ' + shlex.quote(str(Path(gate.__file__).resolve())) + ' hook'
        legacy = {'type': 'command', 'command': command, 'timeout': 37,
                  'async': False, 'statusMessage': 'keep', 'custom': {'value': 1}}
        config = {'description': 'keep', 'hooks': {
            'SessionStart': [{'hooks': [{'type': 'command', 'command': 'unrelated'}]}],
            'Stop': [{'matcher': 'custom', 'hooks': [dict(legacy),
                      {'type': 'command', 'command': 'another', 'commandWindows': 'keep me'}]}]}}
        gate.save(self.home / 'hooks.json', config)
        for old_windows in (None, 'obsolete Windows launcher'):
            with self.subTest(old_windows=old_windows):
                if old_windows:
                    config['hooks']['Stop'][0]['hooks'][0]['commandWindows'] = old_windows
                gate.save(self.home / 'hooks.json', config)
                result = gate.install_hook(self.home)
                actual = gate.read(self.home / 'hooks.json')
                expected = json.loads(json.dumps(config))
                expected['hooks']['Stop'][0]['hooks'][0]['commandWindows'] = result['commandWindows']
                self.assertEqual(actual, expected)
                before = (self.home / 'hooks.json').read_bytes()
                with patch.object(gate, 'save', side_effect=AssertionError('must not rewrite')):
                    self.assertEqual(gate.install_hook(self.home), result)
                self.assertEqual((self.home / 'hooks.json').read_bytes(), before)

    def test_fresh_install_exposes_both_reviewable_commands_once(self):
        result = gate.install_hook(self.home)
        handler = gate.read(self.home / 'hooks.json')['hooks']['Stop'][0]['hooks'][0]
        self.assertEqual(handler['command'], result['command'])
        self.assertEqual(handler['commandWindows'], result['commandWindows'])
        self.assertEqual(result['command'], 'python3 ' + shlex.quote(str(Path(gate.__file__).resolve())) + ' hook')
        self.assertIn('exit $LASTEXITCODE', result['commandWindows'])
        self.assertNotIn('EncodedCommand', result['commandWindows'])
        self.assertNotIn('powershell.exe', result['commandWindows'])
        self.assertEqual(gate.install_hook(self.home), result)
        self.assertEqual(len(gate.read(self.home / 'hooks.json')['hooks']['Stop']), 1)

    @unittest.skipIf(os.name == 'nt' or not shutil.which('sh'), 'requires POSIX sh')
    def test_posix_command_still_runs_the_real_hook(self):
        scripts = self.root / "package's $value & space" / 'scripts'
        shutil.copytree(KIT / 'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(KIT / 'core', scripts.parent / 'core', ignore=shutil.ignore_patterns('__pycache__'))
        with patch.object(gate, '__file__', str(scripts / 'gate.py')):
            command = gate.install_hook(self.home)['command']
        result = subprocess.run(['sh', '-c', command], input=b'{"hook_event_name":"Stop","session_id":"none"}',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, CODEX_HOME=str(self.home)), timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})


@unittest.skipUnless(os.name == 'nt', 'requires actual Windows PowerShell hosts')
class WindowsHookCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='solo-hook-command-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "中文 project's $value & tools"
        self.root.mkdir()
        self.scripts = self.root / 'scripts'
        shutil.copytree(KIT / 'scripts', self.scripts, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(KIT / 'core', self.root / 'core', ignore=shutil.ignore_patterns('__pycache__'))
        self.home = self.root / 'host'
        # Relocate the actual interpreter too: both executable and script paths
        # must survive PowerShell parsing, not merely an inert mock argument.
        runtime = self.root / "Python's $runtime & space"
        runtime.mkdir()
        # A Windows venv executable is a redirector requiring pyvenv.cfg;
        # relocate the base interpreter instead of copying that redirector.
        base_python = Path(getattr(sys, '_base_executable', sys.executable))
        self.python = runtime / base_python.name
        shutil.copy2(base_python, self.python)
        for dll in base_python.parent.glob('*.dll'):
            shutil.copy2(dll, runtime / dll.name)
        self.env = dict(os.environ, CODEX_HOME=str(self.home), PYTHONHOME=sys.base_prefix,
                        PYTHONUTF8='0', PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('PYTHONIOENCODING', None)
        self.shells = [shutil.which(name) for name in ('pwsh.exe', 'powershell.exe')]
        self.assertTrue(all(self.shells), 'both pwsh.exe and powershell.exe are required on Windows')

    def install(self):
        with patch.object(gate, '__file__', str(self.scripts / 'gate.py')), patch.object(sys, 'executable', str(self.python)):
            return gate.install_hook(self.home)['commandWindows']

    def run_shell(self, shell, command, payload):
        return subprocess.run([shell, '-NoProfile', '-Command', command],
                              input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, timeout=30)

    def test_native_exit_codes_and_utf8_stdin_survive_both_hosts(self):
        script = self.scripts / "observe's $input & exit.py"
        script.write_text('import json, sys\npayload=json.loads(sys.stdin.buffer.read().decode("utf-8"))\n'
                          'print(json.dumps({"payload":payload,"args":sys.argv[1:]}, ensure_ascii=True))\n'
                          'sys.exit(payload["exit"])\n', encoding='utf-8')
        command = gate._windows_hook_command(self.python, script)
        for shell in self.shells:
            for code in (0, 1, 2):
                with self.subTest(shell=shell, exit=code):
                    payload = {'text': "中文 $value & single'quote", 'exit': code}
                    # Also override a hostile inherited native-error preference.
                    result = self.run_shell(shell, '$PSNativeCommandUseErrorActionPreference = $true; ' + command, payload)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {'payload': payload, 'args': ['hook']})

    def test_missing_interpreter_and_script_fail_closed(self):
        for shell in self.shells:
            with self.subTest(shell=shell, missing='executable'):
                command = gate._windows_hook_command(self.root / 'missing python.exe', self.scripts / 'gate.py')
                result = self.run_shell(shell, command, {})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(b'Delivery hook failed:', result.stderr)
            with self.subTest(shell=shell, missing='script'):
                command = gate._windows_hook_command(self.python, self.scripts / 'missing gate.py')
                self.assertEqual(self.run_shell(shell, command, {}).returncode, 2)

    def test_installed_hook_blocks_then_releases_and_removes_registration(self):
        command = self.install()
        for index, shell in enumerate(self.shells):
            with self.subTest(shell=shell):
                work, control = self.root / ('候选' + str(index)), self.root / ('控制' + str(index))
                work.mkdir()
                (work / '要求.md').write_text('保存并读回中文值。\n', encoding='utf-8')
                task, plan, adapter = control / '任务.json', control / '计划.json', control / '检查.py'
                session = '中文会话' + str(index)
                gate.start(work, ['要求.md'], ['candidate.py'], task, session, self.home, decision_mode=False)
                adapter.write_text('import unittest, candidate\nclass Business(unittest.TestCase):\n'
                                   ' def test_value(self): self.assertEqual(candidate.value(), "通过")\n', encoding='utf-8')
                source = gate.read(control / 'brief.json')['units'][0]['id']
                gate.save(plan, {'test_file': str(adapter), 'outcomes': [{'id': 'value', 'sources': [source], 'expected': '通过'}],
                                 'checks': [{'id': 'value', 'test': 'Business.test_value', 'covers': ['value']}]})
                gate.seal(task, plan)
                (work / 'candidate.py').write_text('def value(): return "通过"\n', encoding='utf-8')
                payload = {'hook_event_name': 'Stop', 'session_id': session, 'cwd': str(work), 'stop_hook_active': True}
                blocked = self.run_shell(shell, command, payload)
                self.assertEqual(blocked.returncode, 0, blocked.stderr)
                self.assertEqual(json.loads(blocked.stdout)['decision'], 'block')
                self.assertTrue(gate.registry(self.home, session).exists())
                self.assertEqual(gate.check(task, True)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
                released = self.run_shell(shell, command, payload)
                self.assertEqual(released.returncode, 0, released.stderr)
                self.assertEqual(json.loads(released.stdout), {})
                self.assertEqual(gate.read(task)['state'], 'DELIVERED')
                self.assertFalse(gate.registry(self.home, session).exists())


if __name__ == '__main__':
    unittest.main()
