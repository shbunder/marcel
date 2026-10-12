"""The MCP stdio side: what Claude Code sends and what it reads back."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from marcel_tools import server
from marcel_tools.server import Server, caller_for, serve
from marcel_tools.tools import TOOLS

SID = '6f1c2a9e-8b7d-4c3a-9e2f-0a1b2c3d4e5f'


def run(*messages: Any, caller: Any = None) -> list[dict[str, Any]]:
    out: list[str] = []
    s = Server(caller or (lambda name, args: (f'ran {name} {args}', False)), out.append)
    for m in messages:
        s.handle_line(m if isinstance(m, str) else json.dumps(m))
    return [json.loads(line) for line in out]


def test_initialize_lists_tools_and_ping() -> None:
    init, listed, pong = run(
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-06-18'}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'},
    )  # fmt: skip
    assert init['result']['protocolVersion'] == '2025-06-18'
    assert init['result']['capabilities'] == {'tools': {}}
    assert init['result']['serverInfo']['name'] == 'marcel-tools'
    assert 'brain' in init['result']['instructions']
    assert [t['name'] for t in listed['result']['tools']] == [t.name for t in TOOLS]
    assert all(set(t) == {'name', 'description', 'inputSchema'} for t in listed['result']['tools'])
    assert pong == {'jsonrpc': '2.0', 'id': 3, 'result': {}}


def test_initialize_without_a_version_gets_the_default() -> None:
    (init,) = run({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
    assert init['result']['protocolVersion'] == server.DEFAULT_PROTOCOL


def test_a_tool_call_and_an_error_result() -> None:
    ok, bad = run(
        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
         'params': {'name': 'tasks_list', 'arguments': {'state': 'done'}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 7}},
        caller=lambda name, args: (f'{name}:{args}', name == ''),
    )  # fmt: skip
    assert ok['result'] == {'content': [{'type': 'text', 'text': "tasks_list:{'state': 'done'}"}]}
    assert bad['result']['isError'] is True


def test_broken_lines_never_stop_the_server() -> None:
    def boom(name: str, args: Any) -> tuple[str, bool]:
        raise RuntimeError('kaboom')

    answers = run(
        '', 'not json', '[1]',
        {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/list'},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'x'}},
        {'jsonrpc': '2.0', 'method': 'notifications/cancelled'},
        caller=boom,
    )  # fmt: skip
    assert [a.get('error', {}).get('code') for a in answers] == [-32700, -32700, -32601, -32603]
    assert answers[3]['error']['message'] == 'Internal error: kaboom'


def test_without_config_every_tool_says_marcel_is_unreachable() -> None:
    caller = caller_for({})
    text, is_error = caller('tasks_list', {})
    assert is_error
    assert text.startswith('tasks_list did not work: Marcel is unreachable. CLAUDE_CODE_SESSION_ID')


def test_with_config_calls_go_to_the_hub(tmp_path: Path) -> None:
    (tmp_path / 'hub-secret').write_text('hs_x')
    made: list[Any] = []

    class FakeHub:
        def __init__(self, config: Any) -> None:
            made.append(config)

        def call(self, method: str, path: str, body: Any = None, params: Any = None) -> Any:
            return {'items': [], 'more': False}

    caller = caller_for({'CLAUDE_CODE_SESSION_ID': SID, 'CLAUDE_PLUGIN_DATA': str(tmp_path)},
                        FakeHub)  # fmt: skip
    assert caller('tasks_list', {}) == ('No tasks match.', False)
    assert made[0].session_id == SID


def test_serve_reads_lines_and_writes_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.StringIO()
    serve(['{"jsonrpc":"2.0","id":1,"method":"ping"}\n'], out, lambda n, a: ('', False))
    assert json.loads(out.getvalue()) == {'jsonrpc': '2.0', 'id': 1, 'result': {}}

    monkeypatch.setattr('sys.stdin', io.StringIO('{"jsonrpc":"2.0","id":2,"method":"ping"}\n'))
    stdout = io.StringIO()
    monkeypatch.setattr('sys.stdout', stdout)
    monkeypatch.setattr('os.environ', {})
    server.main()
    assert json.loads(stdout.getvalue())['id'] == 2


def test_the_plugin_starts_this_server_with_python3_and_no_install() -> None:
    manifest = Path(__file__).parents[2] / '.claude-plugin' / 'plugin.json'
    entry = json.loads(manifest.read_text())['mcpServers']['tools']
    assert entry == {
        'command': 'python3',
        'args': ['-m', 'marcel_tools'],
        'env': {'PYTHONPATH': '${CLAUDE_PLUGIN_ROOT}/tools/src'},
    }
    src = Path(__file__).parents[1] / 'src'
    assert (src / 'marcel_tools' / '__main__.py').is_file()


def test_the_server_imports_nothing_outside_the_standard_library() -> None:
    import ast
    import sys

    src = Path(__file__).parents[1] / 'src' / 'marcel_tools'
    for path in src.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                top = name.split('.')[0]
                assert top == 'marcel_tools' or top in sys.stdlib_module_names, (path.name, name)
