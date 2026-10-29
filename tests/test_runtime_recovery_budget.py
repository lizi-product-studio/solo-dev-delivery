"""Durable review budgets and safe tool-rejection recovery through Runtime."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from agent_runtime import AgentRuntime, MemoryStore, ToolRegistry
from agent_runtime.journal import RunJournal
from agent_runtime.tools import ToolError
from control_plane.runtime import DeliveryRuntime


class RuntimeRecoveryBudgetTests(unittest.TestCase):
    def control(self, root, *, task_id, max_retries=2):
        workspace = root / 'workspace'
        workspace.mkdir()
        control = DeliveryRuntime(root / 'control.sqlite3', evidence_root=workspace)
        control.create_task({
            'task_id': task_id, 'goal': 'produce the verified report', 'owner': 'runtime',
            'candidate': 'fixture', 'authorization_ref': 'local-test',
            'scope': ['out.json'], 'excluded': ['external-actions'],
            'policy': {'workspace_write': True, 'shell_required': False},
            'budget': {'max_steps': 20, 'max_retries': max_retries},
            'acceptance': [{'id': 'total', 'expected': 'total=42', 'environment': 'local',
                            'assertion': {'type': 'json_pointer_equals', 'path': 'out.json',
                                          'pointer': '/total', 'value': 42}}],
        }, {'fs.read': True, 'fs.write': True, 'shell.exec': False,
            'evidence.hash': True, 'checkpoint.persist': True})
        lease = control.claim(task_id, 'runtime')['lease_id']
        return workspace, control, lease

    def verifier(self, workspace, control, task_id, lease):
        def verify(*_):
            target = workspace / 'out.json'
            if not target.exists() or json.loads(target.read_text(encoding='utf-8')) != {'total': 42}:
                return False
            current = control.get(task_id)
            evidence = {
                'id': 'readback-' + str(len(control.events(task_id))),
                'kind': 'local-readback', 'uri': 'out.json',
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                'claim': 'total=42', 'verdict': 'PASS', 'check_id': 'total', 'environment': 'local',
                **{key: current[key] for key in ('task_id', 'revision', 'contract_hash', 'candidate')},
            }
            control.apply(task_id, lease, 'evidence', evidence,
                          evidence_root=workspace, reviewer_id='host-test-verifier')
            return True
        return verify

    def test_exhausted_review_budget_survives_fresh_runtime_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, control, lease = self.control(root, task_id='retry-budget', max_retries=1)
            model_steps = []
            writes = []
            reviews = []
            tools = ToolRegistry()

            def write_file(path, text):
                self.assertEqual('out.json', path)
                (workspace / path).write_bytes(text.encode('utf-8'))
                writes.append(text)
                return {'written': True}

            tools.register('write_file', write_file)

            def model(request):
                model_steps.append(request['step'])
                return {'action': 'tool', 'name': 'write_file',
                        'arguments': {'path': 'out.json', 'text': 'wrong-' + str(request['step'])}}

            def reject(*_):
                reviews.append('FAIL')
                return False

            reject.ready = lambda events: True  # Explicitly trusted host test callback.
            memory = MemoryStore(root / 'memory.sqlite3')
            first = AgentRuntime(model, tools, memory, max_steps=6).run(
                'produce the verified report', task_id='retry-budget', control=control, lease_id=lease,
                verifier=reject, journal=RunJournal(root / 'journal.sqlite3'), auto_verify=True)
            self.assertEqual('CONTROL_BLOCKED', first.status)
            self.assertEqual([1, 2], model_steps)  # Initial attempt plus one permitted repair.
            previous_bytes = (workspace / 'out.json').read_bytes()

            # Re-create Runtime, memory, control and journal handles. The saved
            # budget, rather than an in-memory counter, must prevent more work.
            restarted_control = DeliveryRuntime(root / 'control.sqlite3', evidence_root=workspace)
            for _ in range(2):
                resumed = AgentRuntime(model, tools, MemoryStore(root / 'memory.sqlite3'), max_steps=6).run(
                    'produce the verified report', task_id='retry-budget', control=restarted_control,
                    lease_id=lease, verifier=reject, journal=RunJournal(root / 'journal.sqlite3'),
                    auto_verify=True, resume=True)
                self.assertEqual('CONTROL_BLOCKED', resumed.status)
                self.assertEqual(first.steps, resumed.steps)
            self.assertEqual([1, 2], model_steps)
            self.assertEqual(2, len(writes))
            self.assertEqual(2, len(reviews))
            self.assertEqual(previous_bytes, (workspace / 'out.json').read_bytes())
            self.assertFalse(any(row['kind'] == 'success' for row in memory.search('verified report')))

    def test_pre_effect_tool_error_can_be_corrected_and_independently_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, control, lease = self.control(root, task_id='safe-repair')
            tools = ToolRegistry()
            successful_writes = []

            def guarded_write(path, value):
                if path != 'out.json':
                    raise ToolError('write rejected before effects: outside authorized scope')
                (workspace / path).write_text(json.dumps(value), encoding='utf-8')
                successful_writes.append(path)
                return {'written': True}

            tools.register('write_json', guarded_write)

            def model(request):
                if request['step'] == 1:
                    return {'action': 'tool', 'name': 'write_json',
                            'arguments': {'path': 'forbidden.json', 'value': {'total': 42}}}
                if request['step'] == 2:
                    self.assertEqual('FAILED', request['observations'][-1]['result']['status'])
                    return {'action': 'tool', 'name': 'write_json',
                            'arguments': {'path': 'out.json', 'value': {'total': 42}}}
                return {'action': 'finish', 'answer': 'report repaired and verified'}

            result = AgentRuntime(model, tools, MemoryStore(root / 'memory.sqlite3'), max_steps=4).run(
                'produce the verified report', task_id='safe-repair', control=control, lease_id=lease,
                verifier=self.verifier(workspace, control, 'safe-repair', lease),
                evidence_root=workspace, reviewer_id='host-test-verifier',
                journal=RunJournal(root / 'journal.sqlite3'))
            self.assertEqual('COMPLETED', result.status)
            self.assertEqual('COMPLETED', control.get('safe-repair')['status'])
            self.assertEqual(3, result.steps)
            self.assertFalse((workspace / 'forbidden.json').exists())
            self.assertEqual(['out.json'], successful_writes)
            self.assertEqual([], control.get('safe-repair')['failed_tool_calls'])
            self.assertEqual('FAILED', result.events[0]['result']['status'])

    def test_arbitrary_tool_status_cannot_claim_pre_effect_rejection(self):
        for outcome in ('FAILED', 'UNKNOWN'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace, control, lease = self.control(root, task_id='untrusted-status')
                tools = ToolRegistry()
                calls = []

                def reported_outcome():
                    return {'status': outcome, 'pre_effect_rejection': True}

                def guarded_write(path, value):
                    if path != 'out.json':
                        raise ToolError('scope rejection before effects')
                    (workspace / path).write_text(json.dumps(value), encoding='utf-8')
                    return {'written': True}

                tools.register('operation', reported_outcome)
                tools.register('write_json', guarded_write)

                def model(request):
                    calls.append(request['step'])
                    if request['step'] == 1:
                        return {'action': 'tool', 'name': 'operation', 'arguments': {}}
                    if request['step'] == 2:
                        return {'action': 'tool', 'name': 'write_json',
                                'arguments': {'path': 'forbidden.json', 'value': {}}}
                    if request['step'] == 3:
                        return {'action': 'tool', 'name': 'write_json',
                                'arguments': {'path': 'out.json', 'value': {'total': 42}}}
                    return {'action': 'finish', 'answer': 'done'}

                journal = RunJournal(root / 'journal.sqlite3')
                runtime = AgentRuntime(model, tools, MemoryStore(root / 'memory.sqlite3'), max_steps=5)
                options = dict(task_id='untrusted-status', control=control, lease_id=lease,
                               verifier=self.verifier(workspace, control, 'untrusted-status', lease),
                               evidence_root=workspace, reviewer_id='host-test-verifier', journal=journal)
                result = runtime.run('produce the verified report', **options)
                self.assertNotEqual('COMPLETED', result.status)
                if outcome == 'UNKNOWN':
                    self.assertEqual('NEEDS_RECONCILIATION', result.status)
                    resumed = runtime.run('produce the verified report', resume=True, **options)
                    self.assertEqual('NEEDS_RECONCILIATION', resumed.status)
                    self.assertEqual([1], calls)
                    self.assertTrue(control.get('untrusted-status')['unknown_tool_calls'])
                else:
                    self.assertEqual('CONTROL_BLOCKED', result.status)
                    self.assertTrue(control.get('untrusted-status')['failed_tool_calls'])
                self.assertFalse((workspace / 'forbidden.json').exists())


if __name__ == '__main__':
    unittest.main()
