"""Tests for the code_exec co-work notebook engine (F3.2).

Covers the three in-process layers: the wire protocol, the persistent-namespace
:class:`CellServer`, and the kernel-side toolkit RPC bridge — plus an
end-to-end wiring test that runs a cell whose ``toolkit()`` call reaches a real
registered handler, with no subprocess or sandbox (that is F3.3).
"""

from __future__ import annotations

import pytest

import marcel_core.toolkit as toolkit_registry
from marcel_core.harness.code_exec import (
    CellReply,
    CellRequest,
    CellServer,
    ToolkitCall,
    ToolkitReply,
    decode,
    encode,
    make_local_call_toolkit,
    serve_toolkit_call,
)


@pytest.fixture
def isolated_toolkit_registry():
    """Snapshot / clear / restore the global toolkit handler registry."""
    saved = dict(toolkit_registry._registry)
    toolkit_registry._registry.clear()
    yield toolkit_registry._registry
    toolkit_registry._registry.clear()
    toolkit_registry._registry.update(saved)


# A call_toolkit that never touches the kernel — for CellServer unit tests.
def _stub_toolkit(replies: dict[str, ToolkitReply]):
    def call(tk: ToolkitCall) -> ToolkitReply:
        return replies.get(tk.tool_id, ToolkitReply(ok=False, error=f'no stub for {tk.tool_id}'))

    return call


def _server(call_toolkit=None, *, user_slug: str = 'alice') -> CellServer:
    return CellServer(call_toolkit or _stub_toolkit({}), user_slug=user_slug)


# --- protocol --------------------------------------------------------------


@pytest.mark.parametrize(
    'msg',
    [
        CellRequest(code='x = 1'),
        CellReply(ok=True, stdout='hi', value_repr='3', error=None),
        CellReply(ok=False, error='Boom'),
        ToolkitCall(tool_id='weather.today', params={'city': 'Ghent'}),
        ToolkitReply(ok=True, value='sunny'),
        ToolkitReply(ok=False, error='no such handler'),
    ],
)
def test_protocol_round_trips(msg):
    assert decode(encode(msg)) == msg


def test_decode_rejects_non_json():
    with pytest.raises(ValueError, match='invalid JSON'):
        decode('not json {')


def test_decode_rejects_unknown_type():
    with pytest.raises(ValueError, match='unknown protocol message type'):
        decode('{"type": "Nope", "data": {}}')


# --- CellServer: namespace + capture --------------------------------------


def test_namespace_persists_across_cells():
    srv = _server()
    assert srv.run_cell('x = 40').ok
    reply = srv.run_cell('x + 2')
    assert reply.ok
    assert reply.value_repr == '42'


def test_defs_persist_across_cells():
    srv = _server()
    srv.run_cell('def double(n):\n    return n * 2')
    assert srv.run_cell('double(21)').value_repr == '42'


def test_stdout_is_captured():
    reply = _server().run_cell('print("hello from cell")')
    assert reply.ok
    assert reply.stdout == 'hello from cell\n'
    assert reply.value_repr is None  # a print statement has no echoed value


def test_trailing_statement_has_no_value_repr():
    reply = _server().run_cell('y = 5')
    assert reply.ok
    assert reply.value_repr is None


def test_expression_returning_none_is_not_echoed():
    reply = _server().run_cell('print("side effect")')
    assert reply.ok
    # print() returns None → nothing echoed, but stdout still captured.
    assert reply.value_repr is None
    assert reply.stdout == 'side effect\n'


def test_empty_cell_is_ok():
    reply = _server().run_cell('   \n  ')
    assert reply == CellReply(ok=True, stdout='', value_repr=None)


def test_user_slug_and_get_logger_injected():
    srv = _server(user_slug='bob')
    assert srv.run_cell('user_slug').value_repr == "'bob'"
    # get_logger is callable inside the cell and returns a usable logger.
    reply = srv.run_cell('get_logger("demo").name')
    assert reply.ok
    assert reply.value_repr == "'marcel.cell.demo'"


# --- CellServer: errors ----------------------------------------------------


def test_syntax_error_reports_not_raises():
    reply = _server().run_cell('def broken(:')
    assert not reply.ok
    assert 'SyntaxError' in (reply.error or '')


def test_runtime_error_trims_traceback_to_cell():
    reply = _server().run_cell('print("before")\nraise ValueError("kaboom")')
    assert not reply.ok
    assert reply.stdout == 'before\n'  # stdout up to the failure is preserved
    assert 'ValueError: kaboom' in (reply.error or '')
    # The traceback shows the cell frame, not run_cell/exec internals.
    assert '<cell>' in (reply.error or '')
    assert 'run_cell' not in (reply.error or '')


def test_server_survives_a_failing_cell():
    srv = _server()
    assert not srv.run_cell('1 / 0').ok
    # Namespace and server are still usable afterwards.
    assert srv.run_cell('7 * 6').value_repr == '42'


# --- CellServer: toolkit() injection --------------------------------------


def test_toolkit_returns_handler_value():
    srv = _server(_stub_toolkit({'greet.hi': ToolkitReply(ok=True, value='hello')}))
    reply = srv.run_cell('toolkit("greet.hi", name="x")')
    assert reply.ok
    assert reply.value_repr == "'hello'"


def test_toolkit_failure_raises_toolkiterror_in_cell():
    srv = _server(_stub_toolkit({'bad.call': ToolkitReply(ok=False, error='handler exploded')}))
    reply = srv.run_cell(
        'try:\n    toolkit("bad.call")\n    caught = "no"\nexcept ToolkitError as e:\n    caught = str(e)\ncaught'
    )
    assert reply.ok
    assert reply.value_repr == "'handler exploded'"


def test_toolkit_forwards_params_and_id():
    seen: list[ToolkitCall] = []

    def spy(tk: ToolkitCall) -> ToolkitReply:
        seen.append(tk)
        return ToolkitReply(ok=True, value='ok')

    _server(spy).run_cell('toolkit("db.write", key="k", val="v")')
    assert seen == [ToolkitCall(tool_id='db.write', params={'key': 'k', 'val': 'v'})]


# ToolkitError is injected into every cell namespace by default so cells can
# catch a failed toolkit() call without importing anything.
def test_toolkiterror_is_available_to_cells():
    assert _server().run_cell('ToolkitError.__name__').value_repr == "'ToolkitError'"


# --- bridge: serve_toolkit_call (kernel side) ------------------------------


async def test_serve_toolkit_call_runs_registered_handler(isolated_toolkit_registry):
    @toolkit_registry.marcel_tool('math.add')
    async def _add(params: dict, user_slug: str) -> str:
        return str(int(params['a']) + int(params['b']))

    reply = await serve_toolkit_call(ToolkitCall(tool_id='math.add', params={'a': 2, 'b': 3}), 'alice')
    assert reply == ToolkitReply(ok=True, value='5')


async def test_serve_toolkit_call_unknown_handler(isolated_toolkit_registry):
    reply = await serve_toolkit_call(ToolkitCall(tool_id='nope.missing'), 'alice')
    assert not reply.ok
    assert 'nope.missing' in (reply.error or '')


async def test_serve_toolkit_call_handler_error_is_caught(isolated_toolkit_registry):
    @toolkit_registry.marcel_tool('boom.now')
    async def _boom(params: dict, user_slug: str) -> str:
        raise RuntimeError('handler failed hard')

    reply = await serve_toolkit_call(ToolkitCall(tool_id='boom.now'), 'alice')
    assert not reply.ok
    assert 'RuntimeError: handler failed hard' in (reply.error or '')


async def test_serve_toolkit_call_forwards_user_slug(isolated_toolkit_registry):
    @toolkit_registry.marcel_tool('whoami.get')
    async def _who(params: dict, user_slug: str) -> str:
        return user_slug

    reply = await serve_toolkit_call(ToolkitCall(tool_id='whoami.get'), 'bob')
    assert reply == ToolkitReply(ok=True, value='bob')


# --- end-to-end: CellServer + real registry via the local bridge -----------


def test_cell_calls_real_toolkit_handler_end_to_end(isolated_toolkit_registry):
    """A cell's toolkit() reaches a real async handler — no subprocess (F3.3)."""

    @toolkit_registry.marcel_tool('greeter.hello')
    async def _hello(params: dict, user_slug: str) -> str:
        return f'hello {params["who"]} from {user_slug}'

    srv = CellServer(make_local_call_toolkit('carol'), user_slug='carol')
    reply = srv.run_cell('msg = toolkit("greeter.hello", who="world")\nmsg.upper()')
    assert reply.ok
    assert reply.value_repr == "'HELLO WORLD FROM CAROL'"


def test_local_bridge_surfaces_handler_error_as_toolkiterror(isolated_toolkit_registry):
    @toolkit_registry.marcel_tool('flaky.op')
    async def _flaky(params: dict, user_slug: str) -> str:
        raise ValueError('nope')

    srv = CellServer(make_local_call_toolkit('carol'))
    reply = srv.run_cell('try:\n    toolkit("flaky.op")\nexcept ToolkitError as e:\n    out = "caught: " + str(e)\nout')
    assert reply.ok
    assert 'ValueError: nope' in (reply.value_repr or '')


def test_local_bridge_reraises_a_catastrophic_driver_failure(monkeypatch):
    """If the async RPC driver itself blows up, the caller sees it — no hang.

    ``serve_toolkit_call`` normally never raises, so this guards the contract
    of the sync driver underneath ``make_local_call_toolkit``: a failure inside
    the worker thread is re-raised on the calling thread rather than swallowed.
    """
    import marcel_core.harness.code_exec.bridge as bridge_mod

    async def _explode(_call, _user_slug):
        raise RuntimeError('driver melted')

    monkeypatch.setattr(bridge_mod, 'serve_toolkit_call', _explode)
    call = make_local_call_toolkit('carol')
    with pytest.raises(RuntimeError, match='driver melted'):
        call(ToolkitCall(tool_id='whatever.op'))
