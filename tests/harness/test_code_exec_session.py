"""End-to-end tests for the code_exec worker + kernel session (F3.3).

These spawn the real worker as a plain subprocess over pipes — the full
protocol, the worker loop, the kernel-side pump, and the toolkit RPC — but
WITHOUT bubblewrap, so they run anywhere (the sandbox confinement itself needs
user namespaces and is covered by the guarded sandbox tests). A worker behind
bwrap is byte-for-byte the same program; only the spawn argv differs.
"""

from __future__ import annotations

import sys

import pytest

import marcel_core.toolkit as toolkit_registry
from marcel_core.harness.code_exec.session import (
    CellSession,
    CellSessionError,
    open_cell_session,
    worker_argv,
)


@pytest.fixture
def isolated_toolkit_registry():
    saved = dict(toolkit_registry._registry)
    toolkit_registry._registry.clear()
    yield toolkit_registry._registry
    toolkit_registry._registry.clear()
    toolkit_registry._registry.update(saved)


async def _open(user_slug: str = 'alice', timeout: float = 10.0) -> CellSession:
    return await open_cell_session(argv=[*worker_argv(), user_slug], user_slug=user_slug, timeout=timeout)


# --- basic exec over a real worker subprocess ------------------------------


async def test_cell_runs_and_echoes_value():
    session = await _open()
    try:
        reply = await session.run_cell('x = 40\nx + 2')
        assert reply.ok
        assert reply.value_repr == '42'
    finally:
        await session.close()


async def test_namespace_persists_across_cells_in_a_session():
    session = await _open()
    try:
        assert (await session.run_cell('acc = 100')).ok
        assert (await session.run_cell('acc += 5')).ok
        assert (await session.run_cell('acc')).value_repr == '105'
    finally:
        await session.close()


async def test_stdout_flows_back_through_the_session():
    session = await _open()
    try:
        reply = await session.run_cell('print("from the sandbox worker")')
        assert reply.ok
        assert reply.stdout == 'from the sandbox worker\n'
    finally:
        await session.close()


async def test_cell_error_comes_back_as_failed_reply():
    session = await _open()
    try:
        reply = await session.run_cell('raise ValueError("boom")')
        assert not reply.ok
        assert 'ValueError: boom' in (reply.error or '')
        # Worker survived — the session is still usable.
        assert (await session.run_cell('1 + 1')).value_repr == '2'
    finally:
        await session.close()


async def test_user_slug_is_visible_in_the_cell():
    session = await _open(user_slug='bob')
    try:
        assert (await session.run_cell('user_slug')).value_repr == "'bob'"
    finally:
        await session.close()


# --- toolkit RPC across the subprocess boundary ----------------------------


async def test_cell_toolkit_call_is_serviced_in_the_kernel(isolated_toolkit_registry):
    @toolkit_registry.marcel_tool('math.mul')
    async def _mul(params: dict, user_slug: str) -> str:
        return str(int(params['a']) * int(params['b']))

    session = await _open()
    try:
        reply = await session.run_cell('int(toolkit("math.mul", a=6, b=7))')
        assert reply.ok
        assert reply.value_repr == '42'
    finally:
        await session.close()


async def test_toolkit_forwards_session_user_slug_not_worker(isolated_toolkit_registry):
    # The kernel services the call with the SESSION's user_slug (its authority),
    # even though the cell's own user_slug var is whatever the worker was given.
    @toolkit_registry.marcel_tool('whoami.get')
    async def _who(params: dict, user_slug: str) -> str:
        return user_slug

    session = await _open(user_slug='carol')
    try:
        reply = await session.run_cell('toolkit("whoami.get")')
        assert reply.value_repr == "'carol'"
    finally:
        await session.close()


async def test_failed_toolkit_call_raises_in_cell(isolated_toolkit_registry):
    session = await _open()
    try:
        reply = await session.run_cell(
            'try:\n    toolkit("nope.missing")\nexcept ToolkitError as e:\n    out = "caught"\nout'
        )
        assert reply.ok
        assert reply.value_repr == "'caught'"
    finally:
        await session.close()


# --- lifecycle: timeout, close, reuse-after-close --------------------------


async def test_runaway_cell_times_out_and_closes_session():
    session = await _open(timeout=1.0)
    try:
        reply = await session.run_cell('while True:\n    pass')
        assert not reply.ok
        assert 'time limit' in (reply.error or '')
        # Session is closed after a timeout; further use is a clean error.
        with pytest.raises(CellSessionError, match='closed'):
            await session.run_cell('1')
    finally:
        await session.close()


async def test_close_is_idempotent_and_reaps_the_worker():
    session = await _open()
    await session.run_cell('1 + 1')
    await session.close()
    assert session._proc.returncode is not None  # worker reaped
    await session.close()  # second close is a no-op


async def test_run_cell_on_a_worker_that_exited_errors_cleanly():
    # A "worker" that exits immediately (not the real worker) — run_cell must
    # raise a clear CellSessionError rather than hang or crash.
    session = await open_cell_session(argv=[sys.executable, '-c', 'pass'], user_slug='x', timeout=5.0)
    try:
        with pytest.raises(CellSessionError, match='worker exited'):
            await session.run_cell('1')
    finally:
        await session.close()


async def test_two_sessions_have_independent_namespaces():
    a = await _open()
    b = await _open()
    try:
        await a.run_cell('secret = "a-only"')
        reply = await b.run_cell('"secret" in dir()')
        assert reply.value_repr == 'False'
    finally:
        await a.close()
        await b.close()


# --- sandboxed spawn: argv assembly (no real bwrap) ------------------------


async def test_open_sandboxed_cell_session_wraps_worker_in_bwrap_no_network(monkeypatch, tmp_path):
    """The sandboxed spawn = bwrap argv + worker + user_slug, egress disabled."""
    import marcel_core.harness.code_exec.session as session_mod
    import marcel_core.harness.sandbox as sandbox_mod

    bwrap_kwargs: dict = {}

    def _fake_bwrap(**kwargs):
        bwrap_kwargs.update(kwargs)
        return ['bwrap', '--fake']

    captured: dict = {}

    async def _fake_open(*, argv, user_slug, timeout):
        captured['argv'] = argv
        captured['user_slug'] = user_slug
        captured['timeout'] = timeout
        return 'SESSION'

    monkeypatch.setattr(sandbox_mod, 'build_bwrap_argv', _fake_bwrap)
    monkeypatch.setattr(session_mod, 'open_cell_session', _fake_open)

    result = await session_mod.open_sandboxed_cell_session(
        workspace=tmp_path, cwd=tmp_path, data_dir=tmp_path, user_slug='dave', timeout=5.0
    )
    assert result == 'SESSION'
    assert bwrap_kwargs['allow_network'] is False  # untrusted cells get no egress
    assert captured['argv'] == ['bwrap', '--fake', *worker_argv(), 'dave']
    assert captured['user_slug'] == 'dave'
    assert captured['timeout'] == 5.0
