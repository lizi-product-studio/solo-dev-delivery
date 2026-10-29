"""Bounded file tools with structured, pre-effect JSON repairs.

``write_file`` is supplied by the host and remains the authority for writes.
These path checks complement that guard; they are not an OS security sandbox.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from .tools import ToolError


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PATCH_OPERATIONS = 128
MAX_POINTER_LENGTH = 4096


def _path(workspace, value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ToolError('path must be a nonempty workspace-relative string')
    path = Path(value)
    if path.is_absolute() or path.drive or '..' in path.parts:
        raise ToolError('path must remain inside workspace')
    try:
        target = (workspace / path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ToolError('cannot resolve workspace path: ' + str(exc)) from exc
    try:
        target.relative_to(workspace)
    except ValueError:
        raise ToolError('path must remain inside workspace') from None
    return target


def _read(workspace, path, *, json_text=False):
    target = _path(workspace, path)
    try:
        with target.open('rb') as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ToolError('file exceeds the %d byte tool limit' % MAX_FILE_BYTES)
        return raw.decode('utf-8-sig' if json_text else 'utf-8')
    except (OSError, UnicodeError) as exc:
        # Reads have no external write effect, so a failed read can safely be
        # repaired by the model instead of creating an UNKNOWN write outcome.
        raise ToolError('cannot read workspace file: ' + str(exc)) from exc


def _serialize(value):
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        size = len(text.encode('utf-8'))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ToolError('value must be finite, serializable JSON: ' + str(exc)) from exc
    if size > MAX_FILE_BYTES:
        raise ToolError('JSON output exceeds the %d byte tool limit' % MAX_FILE_BYTES)
    return text


def _document(workspace, path):
    try:
        value = json.loads(_read(workspace, path, json_text=True))
        # Python's parser accepts NaN, Infinity and overflowed exponents; the
        # JSON tool contract accepts only finite JSON numbers.
        _serialize(value)
        return value
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ToolError):
            raise
        raise ToolError('invalid JSON document: ' + str(exc)) from exc


def _tokens(pointer):
    if not isinstance(pointer, str) or len(pointer) > MAX_POINTER_LENGTH:
        raise ToolError('pointer must be a bounded JSON Pointer string')
    if pointer == '':
        return []
    if not pointer.startswith('/'):
        raise ToolError('JSON Pointer must be empty or start with /')
    tokens = []
    for token in pointer[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ToolError('invalid JSON Pointer escape')
        tokens.append(token.replace('~1', '/').replace('~0', '~'))
    return tokens


def _index(token, size, *, append=False):
    if token == '-' and append:
        return size
    if len(token) > 20 or re.fullmatch(r'0|[1-9][0-9]*', token) is None:
        raise ToolError('array pointer requires an index without leading zeros')
    index = int(token)
    if index >= size + (1 if append else 0):
        raise ToolError('array pointer index is out of bounds')
    return index


def _child(value, token):
    if isinstance(value, dict):
        if token not in value:
            raise ToolError('JSON Pointer member does not exist: ' + token)
        return value[token]
    if isinstance(value, list):
        return value[_index(token, len(value))]
    raise ToolError('JSON Pointer cannot traverse a scalar value')


def _select(value, tokens):
    for token in tokens:
        value = _child(value, token)
    return value


def _patch(document, operations):
    if not isinstance(operations, list) or len(operations) > MAX_PATCH_OPERATIONS:
        raise ToolError('operations must be a list of at most %d patches' % MAX_PATCH_OPERATIONS)
    value = deepcopy(document)
    for operation in operations:
        if (not isinstance(operation, dict) or not isinstance(operation.get('op'), str)
                or operation['op'] not in {'add', 'replace', 'remove'}):
            raise ToolError('supported patch operations are add, replace and remove')
        kind = operation['op']
        expected = {'op', 'path'} | ({'value'} if kind != 'remove' else set())
        if set(operation) != expected:
            raise ToolError('invalid patch fields for ' + kind)
        tokens = _tokens(operation['path'])
        replacement = None
        if kind != 'remove':
            # Validate before effects, and copy so subsequent operations cannot
            # mutate the caller's operation values through shared references.
            replacement = json.loads(_serialize(operation['value']))
        if not tokens:
            if kind == 'remove':
                raise ToolError('removing the entire JSON document is unsupported')
            value = replacement
            continue
        parent = _select(value, tokens[:-1])
        token = tokens[-1]
        if isinstance(parent, dict):
            if kind != 'add' and token not in parent:
                raise ToolError('patch member does not exist: ' + token)
            if kind == 'remove':
                del parent[token]
            else:
                parent[token] = replacement
        elif isinstance(parent, list):
            index = _index(token, len(parent), append=kind == 'add')
            if kind == 'add':
                parent.insert(index, replacement)
            elif kind == 'remove':
                parent.pop(index)
            else:
                parent[index] = replacement
        else:
            raise ToolError('patch target parent is not an object or array')
    return value


def register_file_tools(tools, workspace, write_file):
    """Register file tools; every write calls the supplied host scope guard.

    update_json supports a strict RFC 6902 subset: add/replace/remove, JSON
    Pointer escaping and array insertion/append. Root removal is unsupported.
    Every patch is applied in memory before a single guarded write.
    """
    workspace = Path(workspace).resolve()
    if not callable(write_file):
        raise ToolError('a host write_file guard is required')

    def read_file(path):
        return _read(workspace, path)

    def read_json(path):
        return {'data': _document(workspace, path)}

    def write_json(path, value):
        _path(workspace, path)
        return write_file(path, _serialize(value))

    def update_json(path, operations):
        value = _patch(_document(workspace, path), operations)
        return write_file(path, _serialize(value))

    def write_text_from_json(source_path, pointer, target_path):
        _path(workspace, target_path)
        value = _select(_document(workspace, source_path), _tokens(pointer))
        if not isinstance(value, str):
            raise ToolError('the selected JSON value must be a string')
        try:
            size = len(value.encode('utf-8'))
        except UnicodeError as exc:
            raise ToolError('selected string is not valid UTF-8') from exc
        if size > MAX_FILE_BYTES:
            raise ToolError('selected string exceeds the file tool limit')
        return write_file(target_path, value)

    tools.register('read_file', read_file, description='read a bounded UTF-8 workspace file; arguments: path')
    tools.register('read_json', read_json, description='read finite JSON inside {data: value}; arguments: path')
    tools.register('write_file', write_file, description='write UTF-8 text through the host scope guard; arguments: path, text')
    tools.register('write_json', write_json, description='serialize structured finite JSON; arguments: path, value')
    tools.register('update_json', update_json, description='apply add/replace/remove JSON Pointer patches, then one guarded write; arguments: path, operations (list of {op, path, value}; remove omits value)')
    tools.register('write_text_from_json', write_text_from_json, description='copy a JSON string field exactly into a text artifact through the host scope guard; arguments: source_path, pointer, target_path')
