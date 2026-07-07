"""Tests for the code_exec tool wrapper (F3.3).

The end-to-end worker/session path is covered in
``tests/harness/test_code_exec_session.py``; here we cover the tool's own
logic: the refuse-when-no-sandbox gate, reply formatting, and per-turn session
reuse — all without needing bubblewrap.
"""

from __future__ import annotations

from typing import cast

from pydantic_ai import RunContext

from marcel_core.harness.code_exec.protocol import CellReply
from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.code_exec import _format_reply, code_exec


class _Ctx:
    """Minimal stand-in for RunContext[MarcelDeps] — only .deps is used."""

    def __init__(self, deps: MarcelDeps):
        self.deps = deps


def _ctx(**kw) -> RunContext[MarcelDeps]:
    deps = MarcelDeps(user_slug='alice', conversation_id='c1', channel='cli', model='m', role='admin', **kw)
    return cast(RunContext[MarcelDeps], _Ctx(deps))


# --- reply formatting ------------------------------------------------------


def test_format_reply_stdout_and_value():
    reply = CellReply(ok=True, stdout='line1\n', value_repr='42')
    assert _format_reply(reply) == 'line1\n42'


def test_format_reply_value_only():
    assert _format_reply(CellReply(ok=True, value_repr="'hi'")) == "'hi'"


def test_format_reply_no_output():
    assert _format_reply(CellReply(ok=True)) == '(no output)'


def test_format_reply_error_includes_stdout():
    reply = CellReply(ok=False, stdout='progress\n', error='Traceback...\nValueError: x')
    out = _format_reply(reply)
    assert 'progress' in out
    assert 'ValueError: x' in out


def test_format_reply_truncates_huge_output():
    reply = CellReply(ok=True, stdout='x' * 40000)
    out = _format_reply(reply)
    assert 'Output truncated' in out
    assert len(out) < 40000 + 200


# --- refuse when the sandbox is unavailable --------------------------------


async def test_refuses_when_sandbox_unavailable(monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_code_exec_enabled', True)
    monkeypatch.setattr(settings, 'marcel_sandbox_enabled', True)
    # sandbox_available is imported inside the tool at call time — patch its source.
    import marcel_core.harness.sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod, 'sandbox_available', lambda: False)

    result = await code_exec(_ctx(), 'print(1)')
    assert 'unavailable' in result
    assert 'sandbox' in result


async def test_refuses_when_disabled(monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_code_exec_enabled', False)
    result = await code_exec(_ctx(), 'print(1)')
    assert 'disabled' in result


# --- per-turn session reuse (no real sandbox) ------------------------------


class _FakeSession:
    def __init__(self):
        self.cells: list[str] = []
        self.closed = False

    async def run_cell(self, code: str) -> CellReply:
        self.cells.append(code)
        return CellReply(ok=True, value_repr=repr(len(self.cells)))

    async def close(self):
        self.closed = True


async def test_reuses_the_turn_session_across_cells(monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_code_exec_enabled', True)
    monkeypatch.setattr(settings, 'marcel_sandbox_enabled', True)
    import marcel_core.harness.sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod, 'sandbox_available', lambda: True)

    fake = _FakeSession()

    async def _fake_open(**kw):
        return fake

    monkeypatch.setattr(
        'marcel_core.harness.code_exec.session.open_sandboxed_cell_session',
        _fake_open,
    )

    ctx = _ctx()
    r1 = await code_exec(ctx, 'a = 1')
    r2 = await code_exec(ctx, 'a + 1')
    # Same session object reused → cells accumulate; namespace would persist.
    assert ctx.deps.turn.cell_session is fake
    assert fake.cells == ['a = 1', 'a + 1']
    assert r1 == '1' and r2 == '2'


async def test_session_failure_is_surfaced_not_raised(monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_code_exec_enabled', True)
    monkeypatch.setattr(settings, 'marcel_sandbox_enabled', True)
    import marcel_core.harness.sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod, 'sandbox_available', lambda: True)

    async def _boom(**kw):
        raise RuntimeError('spawn failed')

    monkeypatch.setattr('marcel_core.harness.code_exec.session.open_sandboxed_cell_session', _boom)

    result = await code_exec(_ctx(), 'print(1)')
    assert 'code_exec failed' in result
    assert 'spawn failed' in result
