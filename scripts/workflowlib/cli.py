"""Command-line helpers. Shared verbatim by saaw and its workflow template; edit both copies together."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def quote(text):
    """Quote one argument for the shell that will run the printed command."""
    if os.name == 'nt':
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def launcher(script, root):
    """Command prefix that reruns `script` for the same project root."""
    command = f'{quote(sys.executable)} {quote(str(Path(script).resolve()))}'
    if root != Path.cwd().resolve():
        command += f' --root {quote(str(root))}'
    return command


def read_payload():
    """JSON object from stdin, or None when stdin is empty or a terminal."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    data = sys.stdin.buffer.read()
    text = data.decode('utf-16') if data[:2] in (b'\xff\xfe', b'\xfe\xff') else data.decode('utf-8-sig')
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'Payload is not valid JSON: {error}') from None


def run(action):
    """Print what `action()` returns; print errors to stderr. Returns the exit code."""
    try:
        print(action())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0
