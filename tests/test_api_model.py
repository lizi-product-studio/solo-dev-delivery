"""Real API-provider CLI transport against a loopback-only HTTP model double.

No external model or credentials are used.  The double proposes decisions;
the production CLI still owns tools, JSON serialization and contract acceptance.
"""
from contextlib import closing, contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'portable-agent-kit/scripts/agent_run.py'
MODEL = 'loopback-transport-test'
VALUE = {
    'total': 42,
    'payload': {
        'body': 'hello\nworld "quoted" 中文',
        'path': 'C:\\sandbox\\result',
        'items': [1, True, None, {'name': '测试'}],
    },
}


@contextmanager
def loopback_model(mode, api_key):
    """Capture requests without recording or printing Authorization headers."""
    requests = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *_args):
            pass

        def respond(self, status, body):
            encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            # Never put the received header or the synthetic key in diagnostics.
            if not hmac.compare_digest(
                    self.headers.get('Authorization', ''), 'Bearer ' + api_key):
                errors.append('synthetic bearer authentication was missing')
                self.respond(401, {'error': 'unauthorized test request'})
                return
            try:
                if self.path != '/v1/chat/completions':
                    raise ValueError('unexpected endpoint')
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('unexpected content type')
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                user = [m for m in payload['messages'] if m['role'] == 'user']
                request = json.loads(user[0]['content'])
                requests.append({'payload': payload, 'runtime': request})
                if mode == 'unavailable':
                    self.respond(503, {'error': 'synthetic service unavailable'})
                    return
                if mode == 'invalid-json':
                    content = '{invalid model JSON'
                elif mode == 'non-object':
                    content = json.dumps(['invalid decision object'])
                elif request['step'] == 1:
                    content = json.dumps({
                        'action': 'tool', 'name': 'write_json',
                        'arguments': {'path': 'report.json', 'value': VALUE},
                    }, ensure_ascii=False)
                elif request['step'] == 2:
                    content = json.dumps({'action': 'finish', 'answer': 'structured report ready'})
                else:
                    raise ValueError('unexpected extra model request')
                self.respond(200, {'choices': [{'message': {'content': content}}]})
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(type(exc).__name__ + ': ' + str(exc))
                self.respond(400, {'error': 'invalid test protocol'})

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05),
        name='api-model-loopback',
    )
    thread.start()
    try:
        yield {
            'url': 'http://127.0.0.1:%d/v1/chat/completions' % server.server_port,
            'requests': requests, 'errors': errors,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError('loopback HTTP server did not stop')


class ApiModelTests(unittest.TestCase):
    def assert_rejected_options(self, options, message):
        with tempfile.TemporaryDirectory(prefix='api-options-test-') as temp:
            root = Path(temp)
            argv = [sys.executable, str(CLI), '--goal', 'reject invalid configuration',
                    '--workspace', str(root / 'workspace'),
                    '--memory', str(root / 'memory.sqlite3'),
                    '--receipt', str(root / 'receipt.json'), *options]
            proc = subprocess.run(argv, cwd=root, capture_output=True,
                                  encoding='utf-8', timeout=10)
            self.assertEqual(2, proc.returncode)
            self.assertIn(message, proc.stderr)
            self.assertEqual('', proc.stdout)
            self.assertEqual([], list(root.iterdir()),
                             'invalid provider options must fail before state or workspace creation')

    def test_api_provider_rejects_unsupported_effort(self):
        self.assert_rejected_options(['--provider', 'api', '--effort', 'medium'],
                                     '--effort is supported only with --provider codex')

    def test_model_configuration_isolation_requires_codex_and_explicit_model(self):
        for options in [
                ['--provider', 'api', '--isolate-model-config'],
                ['--provider', 'api', '--model', MODEL, '--isolate-model-config'],
                ['--provider', 'codex', '--isolate-model-config']]:
            with self.subTest(options=options):
                self.assert_rejected_options(options,
                    '--isolate-model-config requires --provider codex and explicit --model')

    def invoke(self, root, mode):
        workspace = root / 'workspace'
        workspace.mkdir()
        contract = {
            'owner': 'api-transport-test', 'candidate': 'structured-api-fixture',
            'scope': ['report.json'], 'excluded': ['external'],
            'authorization_ref': 'local-loopback-test',
            'policy': {'workspace_write': True, 'shell_required': False},
            'acceptance': [{
                'id': key, 'expected': key + ' equals structured fixture', 'environment': 'local',
                'assertion': {'type': 'json_pointer_equals', 'path': 'report.json',
                              'pointer': '/' + key, 'value': VALUE[key]},
            } for key in VALUE],
        }
        contract_path = root / 'contract.json'
        contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding='utf-8')
        receipt_path = root / 'receipt.json'
        api_key = 'synthetic-local-test-' + secrets.token_hex(16)
        # Read only these non-secret OS settings; do not inherit provider keys or proxies.
        env = {name: os.environ[name] for name in
               ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP') if name in os.environ}
        env.update({
            'AGI_LIKE_API_KEY': api_key, 'AGI_LIKE_MODEL': MODEL,
            'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1',
            'NO_PROXY': '127.0.0.1,localhost',
            'HTTP_PROXY': '', 'HTTPS_PROXY': '', 'ALL_PROXY': '',
        })
        argv = [
            sys.executable, str(CLI), '--provider', 'api',
            '--goal', 'produce structured API report', '--task-id', 'api-transport-test',
            '--workspace', str(workspace), '--memory', str(root / 'memory.sqlite3'),
            '--project-id', 'loopback-api-project', '--contract', str(contract_path),
            '--db', str(root / 'control.sqlite3'), '--state-db', str(root / 'state.sqlite3'),
            '--max-steps', '3', '--receipt', str(receipt_path),
        ]
        with loopback_model(mode, api_key) as endpoint:
            env['AGI_LIKE_MODEL_URL'] = endpoint['url']
            # subprocess.run kills and waits for its child on TimeoutExpired.
            proc = subprocess.run(argv, env=env, cwd=root, capture_output=True,
                                  encoding='utf-8', timeout=20)
        self.assertEqual([], endpoint['errors'])
        self.assertTrue(receipt_path.is_file(), 'CLI did not persist its receipt')
        receipt_text = receipt_path.read_text(encoding='utf-8')
        self.assertFalse(api_key in proc.stdout + proc.stderr + receipt_text,
                         'synthetic API key leaked into CLI output or receipt')
        receipt = json.loads(receipt_text)
        summary = json.loads(proc.stdout)
        self.assertEqual(receipt['status'], summary['status'])
        # Explicitly close SQLite before TemporaryDirectory removes databases on Windows.
        with closing(sqlite3.connect(root / 'memory.sqlite3')) as db:
            memories = db.execute('SELECT kind, task_id, project_id, data FROM memories').fetchall()
        return proc, receipt, endpoint['requests'], memories

    def assert_request(self, captured, step):
        payload = captured['payload']
        self.assertEqual(MODEL, payload['model'])
        self.assertEqual(0, payload['temperature'])
        self.assertEqual({'type': 'json_object'}, payload['response_format'])
        self.assertEqual(['system', 'user'], [m['role'] for m in payload['messages']])
        request = captured['runtime']
        self.assertEqual(step, request['step'])
        self.assertEqual('api-transport-test', request['task_id'])
        self.assertIn('write_json', request['tools'])
        self.assertEqual({'total', 'payload'}, {row['id'] for row in request['acceptance']})
        self.assertTrue(request['bindings']['contract_hash'])
        return request

    def test_api_cli_writes_structured_value_and_passes_contract(self):
        with tempfile.TemporaryDirectory(prefix='api-model-test-') as temp:
            root = Path(temp)
            proc, receipt, requests, memories = self.invoke(root, 'success')
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual('COMPLETED', receipt['status'])
            self.assertEqual('COMPLETED', receipt['control_state']['status'])
            self.assertEqual(2, receipt['steps'])
            self.assertEqual(2, len(requests))
            first = self.assert_request(requests[0], 1)
            self.assertEqual([], first['observations'])
            second = self.assert_request(requests[1], 2)
            observation = second['observations'][0]
            self.assertEqual('write_json', observation['decision']['name'])
            self.assertIsInstance(observation['decision']['arguments']['value'], dict)
            self.assertEqual({'path': 'report.json', 'written': True}, observation['result'])
            self.assertEqual(VALUE, json.loads((root / 'workspace/report.json').read_text(encoding='utf-8')))
            evidence = receipt['control_state']['evidence']
            self.assertEqual({'total', 'payload'}, {row['check_id'] for row in evidence})
            self.assertTrue(all(row['host_verified'] for row in evidence))
            successes = [row for row in memories if row[0] == 'success']
            self.assertEqual(1, len(successes))
            self.assertEqual(('api-transport-test', 'loopback-api-project'), successes[0][1:3])
            self.assertEqual(receipt['events'], json.loads(successes[0][3])['events'])

    def assert_model_failure(self, mode, error_fragment):
        with tempfile.TemporaryDirectory(prefix='api-model-test-') as temp:
            root = Path(temp)
            proc, receipt, requests, memories = self.invoke(root, mode)
            self.assertEqual(2, proc.returncode, proc.stderr)
            self.assertEqual('MODEL_ERROR', receipt['status'])
            self.assertIn(error_fragment, receipt['answer'])
            self.assertEqual(1, receipt['steps'])
            self.assertEqual(1, len(requests))
            self.assert_request(requests[0], 1)
            self.assertEqual([], receipt['events'])
            self.assertNotEqual('COMPLETED', receipt['control_state']['status'])
            self.assertEqual([], receipt['control_state']['evidence'])
            self.assertEqual([], list((root / 'workspace').iterdir()))
            self.assertEqual([], memories, 'model failure must not record successful work')

    def test_api_http_503_is_model_error_without_artifact_or_success_memory(self):
        self.assert_model_failure('unavailable', '503')

    def test_api_invalid_model_json_is_model_error_without_artifact_or_success_memory(self):
        self.assert_model_failure('invalid-json', 'Expecting property name')

    def test_api_non_object_model_json_is_model_error_without_artifact_or_success_memory(self):
        self.assert_model_failure('non-object', 'model response must be an object')


if __name__ == '__main__':
    unittest.main()
