import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from agent_runtime.codex_host import CodexCliAdapter, CodexHostError


class FakeSession:
    def __init__(self): self.calls = []
    def tool_result(self, *args, **kwargs): self.calls.append((args, kwargs))


class CodexHostTests(unittest.TestCase):
    def run_fake(self, source, **kwargs):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / 'fake.py'
            fake.write_text(source, encoding='utf-8')
            session = FakeSession()
            events = CodexCliAdapter(fake).run('goal', workspace=temp, session=session, **kwargs)
            return events, session

    def test_stream_is_drained_and_normal_exit_preserved(self):
        source = """import json,sys
sys.stderr.write('warning' * 50000)
print(json.dumps({'type':'item.completed','item':{'id':'x','type':'command_execution','status':'completed','exit_code':0}}))
print(json.dumps({'type':'turn.completed'}))
"""
        events, session = self.run_fake(source, timeout=5)
        self.assertEqual(2, len(events))
        self.assertEqual(('x', 'SUCCEEDED'), session.calls[0][0])

    def test_failed_tool_is_not_reported_success(self):
        _, session = self.run_fake("import json\nprint(json.dumps({'type':'item.completed','item':{'id':'bad','type':'command_execution','status':'completed','exit_code':3}}))\nprint(json.dumps({'type':'turn.completed'}))")
        self.assertEqual('FAILED', session.calls[0][0][1])

    def test_missing_terminal_and_nonzero_after_terminal_are_errors(self):
        for source in ("print('{}')", "print('{\"type\":\"turn.completed\"}');raise SystemExit(2)"):
            with self.subTest(source=source), self.assertRaises(CodexHostError):
                self.run_fake(source)

    def test_timeout_covers_output_loop(self):
        with self.assertRaisesRegex(CodexHostError, 'timeout'):
            self.run_fake('import time;time.sleep(5)', timeout=0.2)

    def test_model_option_is_a_complete_argument_pair(self):
        self.run_fake("import sys\nassert sys.argv[sys.argv.index('-m')+1]=='chosen-model'\nprint('{\"type\":\"turn.completed\"}')", model='chosen-model')


if __name__ == '__main__': unittest.main()
