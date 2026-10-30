"""Conservative, non-executing classification for the native recovery lane.

This is a deadlock escape for inspection, not permission to run arbitrary shell
code. Unknown commands keep the write fence. Returning True never grants host
approval or changes the task's state, budget, or pending effects.
"""
from pathlib import Path
import re
import shlex
import sys


READ_TOOLS = frozenset({'Read', 'read_file', 'list_directory', 'list_dir', 'view_image'})
READ_COMMANDS = frozenset({'get-content', 'get-item', 'get-childitem', 'get-location',
                           'test-path', 'resolve-path', 'select-string'})
RECOVERY_OPERATIONS = frozenset({'status', 'readback', 'recover'})


def _words(command):
    if not isinstance(command, str) or len(command) > 65536:
        return None
    command = command.strip()
    # The call operator is allowed only at the very start of a single command.
    if command.startswith('& '):
        command = command[2:].lstrip()
    # Reject shell composition, interpolation, redirection and script constructs,
    # including inside quotes. False negatives are preferable to executing code.
    if re.search(r'[\r\n;&|<>`$(){}@]', command):
        return None
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        return None
    result = []
    for word in words:
        if word[:1] in {'\"', "'"}:
            if word[-1:] != word[:1]:
                return None
            word = word[1:-1]
        result.append(word)
    return result


def recovery_call(tool_name, tool_input, *, session_id, home, script):
    """Recognize a small inspection API; never execute text to classify it."""
    if not isinstance(tool_input, dict):
        return False
    if tool_name in READ_TOOLS:
        return True
    for operation in ('status', 'readback', 'recover'):
        if tool_name in {'mcp__agi_like_runtime__native_' + operation,
                          'agi_like_runtime.native_' + operation}:
            return tool_input.get('session_id') == session_id
    if tool_name not in {'Bash', 'exec_command', 'shell_command'}:
        return False
    command = tool_input.get('command', tool_input.get('cmd'))
    # The native host can report its launcher as argv rather than shell text.
    if isinstance(command,list) and all(isinstance(v,str) for v in command):
        words=command
    else:
        words = _words(command)
    if not words:
        return False
    # Codex may supply the raw command or its PowerShell -Command wrapper.
    executable = words[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
    if executable in {'pwsh', 'pwsh.exe', 'powershell', 'powershell.exe'}:
        lowered = [w.casefold() for w in words]
        if '-command' not in lowered:
            return False
        index = lowered.index('-command')
        if any(w.casefold() not in {'-noprofile', '-noninteractive', '-nologo'} for w in words[1:index]) or index != len(words)-2:
            return False
        words = _words(words[-1])
        if not words:
            return False
    if words[0].casefold() in READ_COMMANDS:
        return True
    # Only the interpreter running this trusted hook and its own script qualify.
    # A same-named file in the business workspace is not a recovery executable.
    if len(words) < 5 or not Path(words[0]).is_absolute():
        return False
    if Path(words[0]).resolve() != Path(sys.executable).resolve() or Path(words[1]).resolve() != Path(script).resolve():
        return False
    args = words[2:]
    if args[:2] == ['--home', str(Path(home).resolve())]:
        args = args[2:]
    if len(args) != 3 or args[0] not in RECOVERY_OPERATIONS or args[1:] != ['--session-id', session_id]:
        return False
    return True
