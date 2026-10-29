"""Real unittest receipts must remain bound to the delivered candidate bytes.

Model decisions are a labelled in-process double. Review execution, file
artifacts, control state and memory persistence use the public Runtime entry.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portable-agent-kit' / 'scripts'))
import agent_run
from agent_runtime.memory import MemoryStore


class ReviewCandidateBindingTests(unittest.TestCase):
    def invoke(self, root, *, change_after_pass=None):
        workspace = root / 'workspace'
        workspace.mkdir()
        task = workspace / 'TASK.md'
        task.write_text('Produce out.json containing total=42.', encoding='utf-8')
        review = root / 'review.py'
        review.write_text(
            'import json,unittest\nfrom pathlib import Path\n'
            'class BusinessOutcome(unittest.TestCase):\n'
            ' def test_total(self):\n'
            '  self.assertEqual(42,json.loads(Path("out.json").read_text(encoding="utf-8"))["total"])\n',
            encoding='utf-8')
        contract = {
            'owner': 'runtime', 'candidate': 'binding-fixture', 'authorization_ref': 'local-test',
            'scope': ['out.json'], 'inputs': ['TASK.md'], 'excluded': ['external-actions'],
            'policy': {'workspace_write': True, 'shell_required': False},
            # The independent test supplies the business condition. The file
            # evidence alone cannot certify that the total is still correct.
            'acceptance': [{'id': 'report', 'expected': 'correct report', 'environment': 'local',
                            'assertion': {'type': 'file_exists', 'path': 'out.json'}}],
        }
        (root / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
        model_calls = []

        class DecisionDouble:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, request):
                model_calls.append(request['step'])
                if request['step'] == 1:
                    return {'action': 'tool', 'name': 'read_file', 'arguments': {'path': 'TASK.md'}}
                if request['step'] == 2:
                    return {'action': 'tool', 'name': 'write_json',
                            'arguments': {'path': 'out.json', 'value': {'total': 42}}}
                # Do not repair the raced candidate. A false completion cannot
                # be hidden by a later legitimate rewrite in this regression.
                return {'action': 'finish', 'answer': 'the report is ready'}

        real_verify = agent_run.challenge.verify
        observed_review_statuses = []
        changed = False

        def review_then_race(*args, **kwargs):
            nonlocal changed
            summary = real_verify(*args, **kwargs)
            observed_review_statuses.append(summary['status'])
            if change_after_pass and not changed and summary['status'] == 'CHALLENGE_PASS':
                changed = True
                # This simulates a writer in the gap between a completed
                # independent review and the host's final readback evidence.
                if change_after_pass == 'output':
                    (workspace / 'out.json').write_text('{"total":99}', encoding='utf-8')
                elif change_after_pass == 'reviewer':
                    with review.open('a', encoding='utf-8') as stream:
                        stream.write('\n# Reviewer changed after the completed run.\n')
                elif change_after_pass == 'input':
                    task.write_text('Updated business requirement after the review.', encoding='utf-8')
                else:
                    raise AssertionError('unknown race scenario')
            return summary

        with (patch.object(agent_run, 'CodexDecisionModel', DecisionDouble),
              patch.object(agent_run.challenge, 'verify', review_then_race),
              redirect_stdout(io.StringIO())):
            code = agent_run.main([
                '--goal', 'Produce the verified report', '--workspace', str(workspace),
                '--memory', str(root / 'memory.sqlite3'), '--db', str(root / 'control.sqlite3'),
                '--contract', str(root / 'contract.json'), '--receipt', str(root / 'receipt.json'),
                '--review-script', str(review), '--auto-verify', '--max-steps', '3',
            ])
        report = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))
        memories = MemoryStore(root / 'memory.sqlite3', scope=report['memory_scope']).search(
            'Produce the verified report', limit=100)
        return code, report, memories, model_calls, observed_review_statuses

    def test_unchanged_reviewed_candidate_completes_without_a_model_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report, memories, calls, statuses = self.invoke(Path(temp))
            self.assertEqual(0, code)
            self.assertEqual('COMPLETED', report['status'])
            self.assertEqual('COMPLETED', report['control_state']['status'])
            self.assertEqual([1, 2], calls)
            self.assertEqual(['CHALLENGE_PASS'], statuses)
            self.assertTrue(any(row['kind'] == 'success' for row in memories))
            self.assertEqual('CHALLENGE_PASS', agent_run.challenge.status(
                report['independent_reviews'][0]['receipt'])['status'])

    def test_output_changed_after_a_real_pass_cannot_complete_or_teach_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code, report, memories, _, statuses = self.invoke(root, change_after_pass='output')
            self.assertEqual('CHALLENGE_PASS', statuses[0])
            self.assertEqual(2, code)
            self.assertNotEqual('COMPLETED', report['status'])
            self.assertNotEqual('COMPLETED', report['control_state']['status'])
            self.assertEqual(99, json.loads((root / 'workspace' / 'out.json').read_text())['total'])
            self.assertFalse(any(row['kind'] == 'success' for row in memories))
            self.assertEqual('STALE', agent_run.challenge.status(
                report['independent_reviews'][0]['receipt'])['status'])

    def test_reviewer_changed_after_a_real_pass_cannot_complete_or_teach_success(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report, memories, _, statuses = self.invoke(Path(temp), change_after_pass='reviewer')
            self.assertEqual('CHALLENGE_PASS', statuses[0])
            self.assertEqual(2, code)
            self.assertNotEqual('COMPLETED', report['status'])
            self.assertNotEqual('COMPLETED', report['control_state']['status'])
            self.assertFalse(any(row['kind'] == 'success' for row in memories))
            self.assertEqual('STALE', agent_run.challenge.status(
                report['independent_reviews'][0]['receipt'])['status'])

    def test_input_changed_after_a_real_pass_cannot_complete_or_teach_success(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report, memories, _, statuses = self.invoke(Path(temp), change_after_pass='input')
            self.assertEqual('CHALLENGE_PASS', statuses[0])
            self.assertEqual(2, code)
            self.assertNotEqual('COMPLETED', report['status'])
            self.assertFalse(any(row['kind'] == 'success' for row in memories))
            self.assertEqual('STALE', agent_run.challenge.status(
                report['independent_reviews'][0]['receipt'])['status'])


if __name__ == '__main__':
    unittest.main()
