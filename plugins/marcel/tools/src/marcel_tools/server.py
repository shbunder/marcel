"""A tiny MCP stdio server: newline-delimited JSON-RPC, just what Claude Code's tool calls need.

No SDK (as the channel server): `initialize`, `tools/list`, `tools/call` and `ping`. A line that
breaks never stops the server; a tool that fails answers `isError` with a plain sentence.
"""

import json
import logging
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TextIO

from marcel_tools import __version__
from marcel_tools.hub import ConfigProblem, Hub, load_config
from marcel_tools.tools import TOOLS, call

log = logging.getLogger('marcel_tools')

DEFAULT_PROTOCOL = '2024-11-05'
INSTRUCTIONS = (
    'Marcel’s brain tools: start, steer, stop and file threads (tasks_*), keep projects '
    '(projects_*, board_note), save artifacts, read and write memory, set up schedules, notify '
    'the owner and read plan usage. They work only in Marcel’s brain session; a worker reports '
    'with the channel’s report tool instead.'
)

Caller = Callable[[str, Any], tuple[str, bool]]


class Server:
    def __init__(self, caller: Caller, write: Callable[[str], None]) -> None:
        self.caller = caller
        self.write = write

    def handle_line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not an object')
        except ValueError:
            self._error(None, -32700, 'Parse error')
            return
        try:
            self._dispatch(msg)
        except Exception as exc:  # one bad message never stops the server
            log.exception('MCP handler failed on %s', msg.get('method'))
            if msg.get('id') is not None:
                self._error(msg['id'], -32603, f'Internal error: {exc}')

    def _dispatch(self, msg: dict[str, Any]) -> None:
        rid, method = msg.get('id'), msg.get('method')
        params = msg.get('params') or {}
        if method == 'initialize':
            version = params.get('protocolVersion')
            self._result(rid, {
                'protocolVersion': version if isinstance(version, str) else DEFAULT_PROTOCOL,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'marcel-tools', 'version': __version__},
                'instructions': INSTRUCTIONS,
            })  # fmt: skip
        elif method == 'tools/list':
            self._result(rid, {'tools': [tool.listing() for tool in TOOLS]})
        elif method == 'tools/call':
            name = params.get('name') if isinstance(params.get('name'), str) else ''
            text, is_error = self.caller(name, params.get('arguments'))
            result: dict[str, Any] = {'content': [{'type': 'text', 'text': text}]}
            if is_error:
                result['isError'] = True
            self._result(rid, result)
        elif method == 'ping':
            self._result(rid, {})
        elif rid is not None:
            self._error(rid, -32601, f'Method not found: {method}')
        # Notifications (`notifications/initialized`, cancellations) need no answer.

    def _result(self, rid: Any, result: Any) -> None:
        self.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result}))

    def _error(self, rid: Any, code: int, message: str) -> None:
        error = {'code': code, 'message': message}
        self.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'error': error}))


def caller_for(env: Mapping[str, str], hub_factory: Callable[..., Hub] = Hub) -> Caller:
    """Tool calls through the hub, or, without a config, an answer that says what is missing."""
    try:
        hub = hub_factory(load_config(env))
    except ConfigProblem as exc:
        problem = str(exc)
        log.warning('Not connecting to the hub. %s', problem)
        return lambda name, _args: (f'{name} did not work: Marcel is unreachable. {problem}', True)
    return lambda name, args: call(hub, name, args)


def serve(lines: Iterable[str], out: TextIO, caller: Caller) -> None:
    def write(line: str) -> None:
        out.write(line + '\n')
        out.flush()

    server = Server(caller, write)
    for line in lines:
        server.handle_line(line)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='marcel-tools: %(message)s')
    serve(sys.stdin, sys.stdout, caller_for(os.environ))
