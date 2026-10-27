"""Control JSON and trusted Python sources stay UTF-8 on legacy locales."""
import json
import locale
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
import challenge
import gate
from core.sensitivity import selected_probes


class UTF8ProtocolTests(unittest.TestCase):
    emulate_legacy_locale = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='solo-utf8-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '中文项目'
        self.root.mkdir()
        if self.emulate_legacy_locale:
            # Exercise the same missing-encoding bug on UTF-8-default hosts.
            read_text, write_text = Path.read_text, Path.write_text

            def legacy_read(path, encoding=None, errors=None, **kwargs):
                return read_text(path, encoding=encoding or 'cp936', errors=errors, **kwargs)

            def legacy_write(path, data, encoding=None, errors=None, **kwargs):
                return write_text(path, data, encoding=encoding or 'cp936', errors=errors, **kwargs)

            for method, replacement in (('read_text', legacy_read), ('write_text', legacy_write)):
                patcher = patch.object(Path, method, replacement)
                patcher.start()
                self.addCleanup(patcher.stop)
        else:
            self.assertEqual(sys.flags.utf8_mode, 0)
            self.assertIn(locale.getpreferredencoding(False).lower(), ('cp936', 'gbk'))

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_challenge_reuses_current_pass_preserves_failure_and_rejects_stale(self):
        work, control = self.root / '候选', self.root / '审查'
        work.mkdir()
        self.write(work / '要求.md', '返回通过，拒绝错误结果。\n')
        self.write(work / 'candidate.py', 'import os\ndef value():\n return "错误" if os.environ.get("SOLO_UTF8_BAD") else "通过"\n')
        test = control / '检查.py'
        self.write(test, 'import unittest, candidate\nclass Business(unittest.TestCase):\n def test_value(self): self.assertEqual(candidate.value(), "通过")\n')
        packet, passed = control / '候选.json', control / '通过.json'
        challenge.freeze(work, ['要求.md'], ['candidate.py'], packet)
        self.assertEqual(challenge.run(packet, test, passed, 5)['status'], 'CHALLENGE_PASS')
        self.assertEqual(challenge.status(passed)['status'], 'CHALLENGE_PASS')
        raw = passed.read_bytes()
        counterfactual = challenge.run(packet, test, control / '错误变体.json', 5,
                                      patches=[{'path': 'candidate.py', 'before': 'else "通过"', 'after': 'else "错误"'}])
        self.assertEqual((counterfactual['status'], counterfactual['test_summary']['failures']), ('FAIL', 1))
        self.assertEqual(challenge.status(passed)['status'], 'CHALLENGE_PASS')
        with patch.object(challenge, 'run', side_effect=AssertionError('must reuse current evidence')):
            reused = challenge.verify(work, ['要求.md'], ['candidate.py'], test, control / '重复.json', 5)
        self.assertEqual((reused['status'], reused['reused'], reused['executed_tests']), ('CHALLENGE_PASS', True, 0))
        with patch.dict(os.environ, {'SOLO_UTF8_BAD': '1'}):
            failed = control / '失败.json'
            result = challenge.run(packet, test, failed, 5)
        self.assertEqual((result['status'], result['test_summary']['failures']), ('FAIL', 1))
        # A subsequent genuine PASS for identical bytes cannot erase that FAIL.
        self.assertEqual(challenge.run(packet, test, control / '随后通过.json', 5)['status'], 'CHALLENGE_PASS')
        with patch.object(challenge, 'run', side_effect=AssertionError('must retain current failure')):
            reused = challenge.verify(work, ['要求.md'], ['candidate.py'], test, control / '再次复用.json', 5)
        self.assertEqual((reused['status'], reused['reused']), ('FAIL', True))
        self.assertEqual(challenge.status(failed)['status'], 'FAIL')
        self.assertEqual(passed.read_bytes(), raw)
        self.write(work / 'candidate.py', 'def value(): return "已更改"\n')
        self.assertEqual(challenge.status(passed)['status'], 'STALE')

    def test_old_and_current_gate_execute_then_hook_rechecks_before_release(self):
        for modern in (False, True):
            with self.subTest(modern=modern):
                root = self.root / ('新版' if modern else '旧版')
                work, control, host = root / '候选', root / '控制', root / '宿主'
                work.mkdir(parents=True)
                request, app = work / '要求.md', work / 'candidate.py'
                self.write(request, '只允许启用用户并拒绝保存负数。\n')
                self.write(app, '# 待实现\n')
                task, adapter, plan = control / '任务.json', control / '适配器.py', control / '计划.json'
                gate.start(work, ['要求.md'], ['candidate.py'], task, '中文会话', host, decision_mode=modern)
                self.assertEqual(gate.read(task)['format'], 'local-delivery-gate/2' if modern else 'local-delivery-gate/1')
                source_ids = [unit['id'] for unit in gate.read(control / 'brief.json')['units']]
                self.write(adapter, 'import candidate, unittest\nfrom pathlib import Path\n# 持久写入后重读真实候选\ndef admitted(facts): return candidate.admitted(**facts)\nclass Business(unittest.TestCase):\n def test_save(self):\n  candidate.save(7)\n  self.assertEqual(Path("saved.txt").read_text(encoding="utf-8"), "7")\n  with self.assertRaises(ValueError): candidate.save(-1)\n  self.assertEqual(Path("saved.txt").read_text(encoding="utf-8"), "7")\n')
                if modern:
                    value = {'test_file': str(adapter), 'checks': [{'id': '保存', 'test': 'Business.test_save', 'sources': source_ids}],
                             'decisions': [{'id': 'admission', 'sources': source_ids, 'facts': {'enabled': [True, False]},
                                            'rules': [{'when': {'fact': 'enabled', 'op': '==', 'value': True}, 'then': '允许'}],
                                            'default': '拒绝', 'observe': 'admitted'}]}
                else:
                    value = {'test_file': str(adapter), 'outcomes': [{'id': '保存', 'sources': source_ids, 'expected': '负数拒绝且原值保留'}],
                             'checks': [{'id': '保存', 'test': 'Business.test_save', 'covers': ['保存']}]}
                self.write(plan, json.dumps(value, ensure_ascii=False))
                gate.seal(task, plan)
                good = 'from pathlib import Path\ndef admitted(enabled): return "允许" if enabled else "拒绝"\ndef save(value):\n if value < 0: raise ValueError("负数")\n Path("saved.txt").write_text(str(value), encoding="utf-8")\n'
                self.write(app, good)
                payload = {'hook_event_name': 'Stop', 'session_id': '中文会话', 'cwd': str(work)}
                self.assertEqual(gate.hook(payload, host)['decision'], 'block')
                passed = gate.check(task, True)
                self.assertEqual(passed['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
                self.assertEqual(challenge.status(passed['report'])['status'], 'DECLARED_SCOPE_COVERED')
                self.assertEqual(gate.check(task, True)['executed_checks'], [])
                self.write(app, good.replace('if value < 0:', 'if False:'))
                self.assertEqual(gate.check(task, True)['status'], 'BLOCKED')
                self.assertEqual(challenge.status(passed['report'])['status'], 'STALE')
                self.assertEqual(gate.hook(payload, host)['decision'], 'block')
                self.write(app, good)
                self.assertEqual(gate.check(task)['status'], 'DELIVERABLE_WITHIN_DECLARED_SCOPE')
                # Exercise the actual UTF-8 hook pipe, independently of the
                # invoking shell's stdio overrides and file-default emulation.
                env = dict(os.environ, CODEX_HOME=str(host), PYTHONUTF8='0', PYTHONDONTWRITEBYTECODE='1')
                env.pop('PYTHONIOENCODING', None)
                result = subprocess.run([sys.executable, '-B', str(KIT / 'scripts/gate.py'), 'hook'],
                                        input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout.decode('utf-8')), {})
                self.assertEqual(gate.read(task)['state'], 'DELIVERED')
                self.assertFalse(gate.registry(host, '中文会话').exists())

    def test_existing_selected_probe_record_is_not_lost_by_encoding(self):
        from core.sensitivity import recipe
        work, control = self.root / '候选', self.root / '控制'
        work.mkdir()
        test, spec, report = control / '检查.py', control / '风险.json', control / '回执.json'
        self.write(test, '# 受信检查\n')
        self.write(spec, '{"probes": []}')
        challenge.write_new(report, {'format': 'local-sensitivity-result/1',
                                    'request': recipe(work, ['要求.md'], ['candidate.py'], test),
                                    'spec': {'path': str(spec.resolve())}})
        self.assertEqual(selected_probes(work, ['要求.md'], ['candidate.py'], test, control / '新回执.json'), spec.resolve())


@unittest.skipUnless(os.name == 'nt' and not sys.flags.utf8_mode
                     and locale.getpreferredencoding(False).lower() in ('cp936', 'gbk'), 'requires actual Windows cp936 default locale')
class WindowsDefaultUTF8ProtocolTests(UTF8ProtocolTests):
    emulate_legacy_locale = False


if __name__ == '__main__':
    unittest.main()
