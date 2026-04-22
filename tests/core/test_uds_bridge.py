"""Unit tests for the UDS bridge's framing + dispatch logic (ISSUE-f60b09).

These run the bridge's helpers **in-process** using a pair of asyncio
stream objects wired to a real UDS socketpair. The end-to-end tests in
``test_uds_integrations.py`` already cover the subprocess path; this
file covers the framing and JSON-RPC dispatch paths coverage.py cannot
see through a Popen boundary.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct

import pytest

from marcel_core.plugin import _uds_bridge
from marcel_core.skills.integrations import _registry, register


@pytest.fixture
def clean_registry():
    saved = dict(_registry)
    _registry.clear()
    yield
    _registry.clear()
    _registry.update(saved)


async def _pipe() -> tuple[asyncio.StreamReader, asyncio.StreamWriter, asyncio.StreamReader, asyncio.StreamWriter]:
    """Create a connected pair of asyncio stream reader/writer tuples over a UDS socketpair."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    a.setblocking(False)
    b.setblocking(False)
    ra, wa = await asyncio.open_connection(sock=a)
    rb, wb = await asyncio.open_connection(sock=b)
    return ra, wa, rb, wb


def _framed(body: bytes) -> bytes:
    return struct.pack('>I', len(body)) + body


async def _read_response(reader: asyncio.StreamReader) -> dict:
    """Read one frame and parse as JSON — raises if peer closed without writing."""
    frame = await _uds_bridge._read_frame(reader)
    assert frame is not None, 'peer closed without writing a response'
    return json.loads(frame)


@pytest.mark.asyncio
async def test_write_frame_and_read_frame_round_trip():
    """A body written by ``_write_frame`` is recovered by ``_read_frame``."""
    ra, wa, rb, wb = await _pipe()
    try:
        _uds_bridge._write_frame(wa, b'{"hello": "world"}')
        await wa.drain()
        wa.close()
        got = await _uds_bridge._read_frame(rb)
        assert got == b'{"hello": "world"}'
    finally:
        wb.close()


@pytest.mark.asyncio
async def test_read_frame_returns_none_on_clean_eof():
    """``_read_frame`` returns None (not raise) when the peer closes without writing."""
    ra, wa, rb, wb = await _pipe()
    try:
        wa.close()
        await wa.wait_closed()
        got = await _uds_bridge._read_frame(rb)
        assert got is None
    finally:
        wb.close()


@pytest.mark.asyncio
async def test_read_frame_rejects_oversized_frame():
    """Frames above ``_MAX_FRAME`` raise to prevent memory exhaustion."""
    ra, wa, rb, wb = await _pipe()
    try:
        # Declare a 1 GiB frame — far above the 8 MiB cap.
        wa.write(struct.pack('>I', 1 << 30))
        await wa.drain()
        with pytest.raises(ValueError, match='frame too large'):
            await _uds_bridge._read_frame(rb)
    finally:
        wa.close()
        wb.close()


def test_error_response_shape():
    """Error frames follow JSON-RPC 2.0: jsonrpc/id/error with code + message."""
    body = _uds_bridge._error_response(42, -32601, 'method not found')
    parsed = json.loads(body)
    assert parsed == {
        'jsonrpc': '2.0',
        'id': 42,
        'error': {'code': -32601, 'message': 'method not found'},
    }


@pytest.mark.asyncio
async def test_handle_client_dispatches_success(clean_registry):
    """A valid request gets the handler's result echoed back with the same id."""

    @register('bridge_test.ok')
    async def _ok(params: dict, user_slug: str) -> str:
        return f'hi {user_slug} / {params["x"]}'

    ra, wa, rb, wb = await _pipe()
    task = asyncio.create_task(_uds_bridge._handle_client(ra, wa))
    try:
        req = json.dumps(
            {
                'jsonrpc': '2.0',
                'id': 7,
                'method': 'bridge_test.ok',
                'params': {'params': {'x': 42}, 'user_slug': 'alice'},
            }
        ).encode()
        wb.write(_framed(req))
        await wb.drain()
        resp = await _read_response(rb)
        assert resp['id'] == 7
        assert resp['result'] == 'hi alice / 42'
    finally:
        wb.close()
        await wb.wait_closed()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_handle_client_reports_method_not_found(clean_registry):
    """Unknown method name → JSON-RPC -32601, caller connection stays open for the next call."""
    ra, wa, rb, wb = await _pipe()
    task = asyncio.create_task(_uds_bridge._handle_client(ra, wa))
    try:
        req = json.dumps(
            {
                'jsonrpc': '2.0',
                'id': 11,
                'method': 'nothing.here',
                'params': {'params': {}, 'user_slug': 'alice'},
            }
        ).encode()
        wb.write(_framed(req))
        await wb.drain()
        resp = await _read_response(rb)
        assert resp['id'] == 11
        assert resp['error']['code'] == -32601
        assert 'method not found' in resp['error']['message']
    finally:
        wb.close()
        await wb.wait_closed()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_handle_client_reports_handler_exception(clean_registry):
    """A handler that raises → JSON-RPC -32000, connection stays open."""

    @register('bridge_test.boom')
    async def _boom(params: dict, user_slug: str) -> str:
        raise RuntimeError('kaboom')

    ra, wa, rb, wb = await _pipe()
    task = asyncio.create_task(_uds_bridge._handle_client(ra, wa))
    try:
        req = json.dumps(
            {
                'jsonrpc': '2.0',
                'id': 3,
                'method': 'bridge_test.boom',
                'params': {'params': {}, 'user_slug': 'alice'},
            }
        ).encode()
        wb.write(_framed(req))
        await wb.drain()
        resp = await _read_response(rb)
        assert resp['id'] == 3
        assert resp['error']['code'] == -32000
        assert 'RuntimeError' in resp['error']['message']
        assert 'kaboom' in resp['error']['message']
    finally:
        wb.close()
        await wb.wait_closed()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_handle_client_reports_parse_error(clean_registry):
    """Malformed JSON in the frame → JSON-RPC -32700, connection stays open."""
    ra, wa, rb, wb = await _pipe()
    task = asyncio.create_task(_uds_bridge._handle_client(ra, wa))
    try:
        wb.write(_framed(b'{this is not json'))
        await wb.drain()
        resp = await _read_response(rb)
        assert resp['error']['code'] == -32700
        assert 'parse error' in resp['error']['message']
    finally:
        wb.close()
        await wb.wait_closed()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_handle_client_multiple_requests_on_one_connection(clean_registry):
    """Single connection carries multiple request/response pairs — the accept loop stays open."""

    @register('bridge_test.counter')
    async def _counter(params: dict, user_slug: str) -> str:
        return str(int(params['n']) * 2)

    ra, wa, rb, wb = await _pipe()
    task = asyncio.create_task(_uds_bridge._handle_client(ra, wa))
    try:
        for i in range(3):
            req = json.dumps(
                {
                    'jsonrpc': '2.0',
                    'id': i,
                    'method': 'bridge_test.counter',
                    'params': {'params': {'n': i}, 'user_slug': 'alice'},
                }
            ).encode()
            wb.write(_framed(req))
            await wb.drain()
            resp = await _read_response(rb)
            assert resp['id'] == i
            assert resp['result'] == str(i * 2)
    finally:
        wb.close()
        await wb.wait_closed()
        await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# _load_habitat — import contract
# ---------------------------------------------------------------------------


def test_load_habitat_rejects_missing_init(tmp_path):
    """A habitat dir without __init__.py is an immediate error."""
    with pytest.raises(RuntimeError, match='missing __init__.py'):
        _uds_bridge._load_habitat(tmp_path)


def test_load_habitat_imports_and_populates_registry(tmp_path, clean_registry):
    """_load_habitat runs the habitat's @register decorators."""
    (tmp_path / '__init__.py').write_text(
        'from marcel_core.plugin import register\n'
        '@register("fixture_hab.probe")\n'
        'async def probe(params, user_slug):\n'
        '    return "alive"\n'
    )
    _uds_bridge._load_habitat(tmp_path)
    assert 'fixture_hab.probe' in _registry


# ---------------------------------------------------------------------------
# main() entry point — argv validation
# ---------------------------------------------------------------------------


def test_main_wrong_argv_exits_with_error(capsys):
    rc = _uds_bridge.main(['prog'])  # missing args
    assert rc == 2
    err = capsys.readouterr().err
    assert 'usage' in err
