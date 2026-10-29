"""Reviewer diagnostics remain readable across legacy parent stdio locales."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('challenge_feedback_encoding', KIT / 'scripts/challenge.py')
challenge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(challenge)


class ChallengeFeedbackEncodingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='solo-feedback-encoding-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'candidate'
        self.work.mkdir()
        (self.work / 'request.txt').write_text('输出应与原始要求一致。\n', encoding='utf-8')
        (self.work / 'result.txt').write_text('真实产物\n', encoding='utf-8')
        self.packet = self.root / 'packet.json'
        challenge.freeze(self.work, ['request.txt'], ['result.txt'], self.packet)
        self.test = self.root / 'review.py'

    def run_review(self, source):
        self.test.write_text(source, encoding='utf-8')
        # A real redirected child is forced to the legacy encoding on every OS
        # unless the host runner establishes its own UTF-8 log protocol.
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'cp936', 'PYTHONUTF8': '0'}):
            result = challenge.run(self.packet, self.test, self.root / 'receipt.json', 5)
        receipt = json.loads((self.root / 'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(result, receipt)
        return result

    def test_chinese_failure_stdout_stderr_and_traceback_stay_readable(self):
        result = self.run_review(
            'import sys, unittest\n'
            'print("标准输出：正在校验材料证明")\n'
            'print("标准错误：不得忽略未通过项", file=sys.stderr)\n'
            'class Business(unittest.TestCase):\n'
            ' def test_materials(self):\n'
            '  self.assertEqual("甲", "乙", "材料证明与草稿正文不一致")\n')
        self.assertEqual('FAIL', result['status'])
        self.assertEqual(1, result['test_summary']['failures'])
        self.assertEqual([{'id': 'Business.test_materials', 'status': 'FAIL'}], result['test_cases'])
        self.assertIn('标准输出：正在校验材料证明', result['stdout']['text'])
        self.assertIn('标准错误：不得忽略未通过项', result['stderr']['text'])
        self.assertIn('材料证明与草稿正文不一致', result['stderr']['text'])
        self.assertIn('AssertionError:', result['stderr']['text'])
        self.assertNotIn('\ufffd', result['stdout']['text'] + result['stderr']['text'])
        self.assertEqual('FAIL', challenge.status(self.root / 'receipt.json')['status'])

    def test_utf8_only_text_does_not_turn_a_real_pass_into_unknown(self):
        result = self.run_review(
            'import sys, unittest\n'
            'from pathlib import Path\n'
            'print("UTF-8 输出：🧪")\n'
            'print("UTF-8 错误流：🧪", file=sys.stderr)\n'
            'class Business(unittest.TestCase):\n'
            ' def test_readback(self):\n'
            '  self.assertEqual("真实产物", Path("result.txt").read_text(encoding="utf-8").strip())\n')
        self.assertEqual('CHALLENGE_PASS', result['status'])
        self.assertEqual(1, result['test_summary']['tests'])
        self.assertIn('UTF-8 输出：🧪', result['stdout']['text'])
        self.assertIn('UTF-8 错误流：🧪', result['stderr']['text'])
        self.assertEqual('CHALLENGE_PASS', challenge.status(self.root / 'receipt.json')['status'])


if __name__ == '__main__':
    unittest.main()
