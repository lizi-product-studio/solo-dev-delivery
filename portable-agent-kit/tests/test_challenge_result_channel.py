"""Real runner results survive bounded logs and unavailable control output."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('challenge_result_channel', KIT / 'scripts/challenge.py')
challenge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(challenge)


class ResultChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='solo-result-channel-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'candidate'
        self.work.mkdir()
        (self.work / 'REQUEST.md').write_text('Increment returns the successor.\n', encoding='utf-8')
        (self.work / 'candidate.py').write_text('def increment(value): return value + 1\n', encoding='utf-8')
        self.packet = self.root / 'packet.json'
        self.test = self.root / 'checks.py'
        challenge.freeze(self.work, ['REQUEST.md'], ['candidate.py'], self.packet)

    def write_suite(self, count=1, wrong=False, extra=''):
        self.test.write_text(
            'import atexit, json, sys, unittest\nfrom pathlib import Path\nimport candidate\n'
            'class Business(unittest.TestCase): pass\n'
            'def make_test(value):\n'
            ' def test(self):\n'
            '  self.assertEqual(candidate.increment(value), value + '
            + ('(2 if value == 0 else 1)' if wrong else '1') + ')\n'
            ' return test\n'
            'for value in range(' + str(count) + '):\n'
            " setattr(Business, 'test_increment_with_representative_integer_input_%04d' % value, make_test(value))\n"
            + extra, encoding='utf-8')

    def run_suite(self, name='receipt.json', selected=None):
        path = self.root / name
        result = challenge.run(self.packet, self.test, path, 10, selected)
        self.assertEqual(result, json.loads(path.read_text(encoding='utf-8')))
        return result

    def test_large_pass_records_every_real_test_and_reuses_full_suite(self):
        self.write_suite(1000)
        first = challenge.verify(self.work, ['REQUEST.md'], ['candidate.py'], self.test,
                                 self.root / 'full.json', 10)
        self.assertEqual(('CHALLENGE_PASS', 1000), (first['status'], first['executed_tests']))
        receipt = json.loads(Path(first['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(1000, len(receipt['test_cases']))
        self.assertTrue(all(c['status'] == 'PASS' for c in receipt['test_cases']))
        with patch.object(challenge, 'run', side_effect=AssertionError('must reuse actual full evidence')):
            again = challenge.verify(self.work, ['REQUEST.md'], ['candidate.py'], self.test,
                                     self.root / 'reuse.json', 10)
        self.assertEqual(('CHALLENGE_PASS', True, 0),
                         (again['status'], again['reused'], again['executed_tests']))

    def test_large_suite_retains_one_real_failure_and_selected_filter(self):
        self.write_suite(1000, wrong=True)
        result = self.run_suite()
        self.assertEqual(('FAIL', 1000, 1),
                         (result['status'], result['test_summary']['tests'], result['test_summary']['failures']))
        self.assertEqual(1000, len(result['test_cases']))
        self.assertEqual(1, sum(c['status'] == 'FAIL' for c in result['test_cases']))
        selected = ['Business.test_increment_with_representative_integer_input_0001']
        partial = self.run_suite('selected.json', selected)
        self.assertEqual(('CHALLENGE_PASS', 1), (partial['status'], partial['test_summary']['tests']))
        self.assertEqual([{'id': selected[0], 'status': 'PASS'}], partial['test_cases'])
        # A later passing selection cannot hide the same candidate's full FAIL.
        again = challenge.verify(self.work, ['REQUEST.md'], ['candidate.py'], self.test,
                                 self.root / 'verified.json', 10)
        self.assertEqual(('FAIL', True), (again['status'], again['reused']))

    def test_log_noise_is_truncated_without_losing_real_results(self):
        self.write_suite(extra="atexit.register(lambda: print('noise' * 20000))\n")
        result = self.run_suite()
        self.assertEqual(('CHALLENGE_PASS', 1), (result['status'], result['test_summary']['tests']))
        self.assertTrue(result['stdout']['truncated'])
        self.assertGreater(result['stdout']['bytes'], challenge.LIMIT)
        self.assertLessEqual(len(result['stdout']['text'].encode('utf-8')), challenge.LIMIT)

    def test_missing_corrupt_or_unwritable_result_has_persistent_unknown(self):
        actions = {
            'missing': "atexit.register(lambda: Path(sys.argv[3]).unlink(missing_ok=True))\n",
            'bad_json': "atexit.register(lambda: Path(sys.argv[3]).write_text('{broken', encoding='utf-8'))\n",
            'bad_shape': "atexit.register(lambda: Path(sys.argv[3]).write_text('{}', encoding='utf-8'))\n",
            'bad_encoding': "atexit.register(lambda: Path(sys.argv[3]).write_bytes(b'\\xff'))\n",
            'write_failure': 'Path(sys.argv[3]).mkdir()\n',
        }
        for name, action in actions.items():
            with self.subTest(name=name):
                self.write_suite(extra=action)
                result = self.run_suite(name + '.json')
                self.assertEqual('UNKNOWN', result['status'])
                self.assertIsNone(result['test_summary'])
                self.assertEqual([], result['test_cases'])
                self.assertEqual('FAILED', result['result_channel']['status'])
                self.assertTrue(result['result_channel']['error'])
                self.assertIn('Ran 1 test', result['stderr']['text'])

    def test_business_stdout_marker_cannot_supply_missing_results(self):
        self.write_suite(extra="""def remove_and_print_fake():
 Path(sys.argv[3]).unlink(missing_ok=True)
 print('SOLO_CHALLENGE_RESULT=' + json.dumps({'tests':999, 'failures':0, 'errors':0, 'skipped':0, 'expected_failures':0, 'unexpected_successes':0, 'cases':[]}))
atexit.register(remove_and_print_fake)
""")
        result = self.run_suite()
        self.assertEqual('UNKNOWN', result['status'])
        self.assertIsNone(result['test_summary'])
        self.assertEqual('FAILED', result['result_channel']['status'])


if __name__ == '__main__':
    unittest.main()
