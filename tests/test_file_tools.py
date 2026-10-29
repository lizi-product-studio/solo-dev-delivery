import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable-agent-kit'))
from agent_runtime.file_tools import register_file_tools
from agent_runtime.tools import ToolError, ToolRegistry


class FileToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.writes = []
        self.allowed = {'data.json', 'artifact.txt'}

        def guarded_write(path, text):
            if path not in self.allowed:
                raise ToolError('write is outside the contract scope')
            self.writes.append(path)
            (self.workspace / path).write_bytes(text.encode('utf-8'))
            return {'written': True, 'path': path}

        self.tools = ToolRegistry()
        register_file_tools(self.tools, self.workspace, guarded_write)

    def put(self, value):
        (self.workspace / 'data.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def call(self, name, **arguments):
        return self.tools.call(name, arguments)

    def test_patch_repairs_multiple_fields_with_one_guarded_write(self):
        self.put({'status': 'DRAFT', 'questions': [{'name': 'old'}, {'name': 'extra'}], 'a/b': {'~label': '旧值'}})
        self.call('update_json', path='data.json', operations=[
            {'op': 'remove', 'path': '/questions/1'},
            {'op': 'replace', 'path': '/questions/0/name', 'value': 'new'},
            {'op': 'add', 'path': '/questions/-', 'value': {'name': 'final'}},
            {'op': 'replace', 'path': '/a~1b/~0label', 'value': '中文\n"quoted"'},
        ])
        value = self.call('read_json', path='data.json')
        self.assertEqual('DRAFT', value['data']['status'])
        self.assertNotIn('status', value)
        self.assertEqual([{'name': 'new'}, {'name': 'final'}], value['data']['questions'])
        self.assertEqual('中文\n"quoted"', value['data']['a/b']['~label'])
        self.assertEqual(['data.json'], self.writes)

    def test_failed_late_patch_keeps_original_file_and_does_not_write(self):
        self.put({'a': 1, 'items': [1]})
        original = (self.workspace / 'data.json').read_bytes()
        with self.assertRaises(ToolError):
            self.call('update_json', path='data.json', operations=[
                {'op': 'replace', 'path': '/a', 'value': 2},
                {'op': 'remove', 'path': '/items/5'},
            ])
        self.assertEqual(original, (self.workspace / 'data.json').read_bytes())
        self.assertEqual([], self.writes)

    def test_invalid_patches_are_rejected_before_any_write(self):
        self.put({'items': [1], 'scalar': False})
        bad_operations = [
            [{'op': 'replace', 'path': '/missing', 'value': 1}],
            [{'op': 'remove', 'path': '/items/-'}],
            [{'op': 'add', 'path': '/items/01', 'value': 1}],
            [{'op': 'add', 'path': '/items/2', 'value': 1}],
            [{'op': 'replace', 'path': '/items/-1', 'value': 1}],
            [{'op': 'replace', 'path': '/scalar/x', 'value': 1}],
            [{'op': 'add', 'path': '/bad~2key', 'value': 1}],
            [{'op': 'move', 'path': '/items/0', 'from': '/items/0'}],
            [{'op': [], 'path': '/items/0'}],
            [{'op': 'remove', 'path': None}],
            ['not an operation object'],
            [{'op': 'replace', 'path': '/scalar'}],
            [{'op': 'remove', 'path': '', 'value': 1}],
            [{'op': 'remove', 'path': ''}],
            [{'op': 'add', 'path': '/number', 'value': float('nan')}],
            [{'op': 'add', 'path': '/number', 'value': float('inf')}],
        ]
        for operations in bad_operations:
            with self.subTest(operations=operations), self.assertRaises(ToolError):
                self.call('update_json', path='data.json', operations=operations)
        self.assertEqual([], self.writes)

    def test_root_replace_and_array_insert_follow_pointer_semantics(self):
        self.put({'old': 1})
        self.call('update_json', path='data.json', operations=[
            {'op': 'replace', 'path': '', 'value': ['a', 'c']},
            {'op': 'add', 'path': '/1', 'value': 'b'},
            {'op': 'add', 'path': '/3', 'value': 'd'},
            {'op': 'replace', 'path': '/0', 'value': 'A'},
        ])
        self.assertEqual(['A', 'b', 'c', 'd'], self.call('read_json', path='data.json')['data'])
        self.assertEqual(['data.json'], self.writes)

    def test_text_artifact_is_exactly_derived_from_json_and_uses_scope_guard(self):
        text = '中文标题\r\nline "quoted"\n'
        self.put({'content/field': text})
        self.call('write_text_from_json', source_path='data.json', pointer='/content~1field', target_path='artifact.txt')
        self.assertEqual(text.encode('utf-8'), (self.workspace / 'artifact.txt').read_bytes())
        with self.assertRaises(ToolError):
            self.call('write_text_from_json', source_path='data.json', pointer='/content~1field', target_path='unauthorized.txt')
        self.assertFalse((self.workspace / 'unauthorized.txt').exists())
        self.assertEqual(['artifact.txt'], self.writes)

    def test_nonstring_or_missing_selected_field_does_not_create_artifact(self):
        self.put({'body': {'text': 'nested'}})
        for pointer in ('/body', '/missing', '/body/text/nope'):
            with self.subTest(pointer=pointer), self.assertRaises(ToolError):
                self.call('write_text_from_json', source_path='data.json', pointer=pointer, target_path='artifact.txt')
        self.assertFalse((self.workspace / 'artifact.txt').exists())
        self.assertEqual([], self.writes)

    def test_read_errors_are_pre_effect_tool_errors_and_bom_is_accepted(self):
        for raw in (b'{bad', b'{"x": NaN}', b'{"x": 1e10000}', b'\xff'):
            (self.workspace / 'data.json').write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ToolError):
                self.call('read_json', path='data.json')
        with self.assertRaises(ToolError):
            self.call('read_file', path='missing.txt')
        (self.workspace / 'data.json').write_bytes(b'\xef\xbb\xbf{"ok":true}')
        self.assertEqual({'data': {'ok': True}}, self.call('read_json', path='data.json'))
        self.assertEqual([], self.writes)

    def test_parent_absolute_and_symlink_escape_reads_are_rejected(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"private":true}', encoding='utf-8')
        for path in ('../outside.json', str(outside)):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.call('read_json', path=path)
        link = self.workspace / 'link.json'
        try:
            link.symlink_to(outside)
        except OSError:
            return  # Windows accounts without symlink privilege still test traversal.
        with self.assertRaises(ToolError):
            self.call('read_json', path='link.json')

    def test_writer_checks_json_before_effect_and_preserves_guard_exceptions(self):
        for value in (float('nan'), {'x': float('-inf')}, object()):
            with self.subTest(value=value), self.assertRaises(ToolError):
                self.call('write_json', path='data.json', value=value)
        with self.assertRaises(ToolError):
            self.call('write_json', path='forbidden.json', value={'x': 1})
        self.assertEqual([], self.writes)
        self.assertFalse((self.workspace / 'data.json').exists())

        def uncertain_write(path, text):
            (self.workspace / path).write_text(text, encoding='utf-8')
            raise OSError('write response lost')

        register_file_tools(self.tools, self.workspace, uncertain_write)
        with self.assertRaises(OSError):
            self.call('write_json', path='data.json', value={'x': 1})
        self.assertTrue((self.workspace / 'data.json').exists())

    def test_bounded_reads_patches_and_serialization_do_not_write(self):
        self.put({'x': 'large contents'})
        with patch('agent_runtime.file_tools.MAX_FILE_BYTES', 10):
            with self.assertRaises(ToolError):
                self.call('read_json', path='data.json')
            with self.assertRaises(ToolError):
                self.call('write_json', path='data.json', value={'long': 'many contents'})
        with patch('agent_runtime.file_tools.MAX_PATCH_OPERATIONS', 1):
            with self.assertRaises(ToolError):
                self.call('update_json', path='data.json', operations=[
                    {'op': 'add', 'path': '/a', 'value': 1},
                    {'op': 'add', 'path': '/b', 'value': 2}])
        self.assertEqual([], self.writes)


if __name__ == '__main__':
    unittest.main()
