"""The ``code_exec`` tool — a per-turn sandboxed Python notebook (F3).

Admin-tier. Runs a Python **cell** in a persistent, sandboxed namespace and
returns its output. The session lives on the turn
(``ctx.deps.turn.cell_session``) so successive cells build on each other; the
runner closes it at turn end.

Requires the bubblewrap sandbox. Unlike ``bash`` — which falls back to an
unsandboxed run under the command policy — ``code_exec`` runs code the model
wrote, so with no sandbox it **refuses** rather than execute cells unconfined.
The proven-script → durable-tool half of the co-work loop is
:mod:`marcel_core.tools.promote`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

_MAX_OUTPUT = 30000
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _unavailable_reason() -> str | None:
    """Return why code_exec cannot run, or ``None`` when it can."""
    from marcel_core.config import settings
    from marcel_core.harness.sandbox import sandbox_available

    if not settings.marcel_code_exec_enabled:
        return 'code_exec is disabled on this instance (marcel_code_exec_enabled is off).'
    if not (settings.marcel_sandbox_enabled and sandbox_available()):
        return (
            'code_exec is unavailable: it requires the execution sandbox '
            '(bubblewrap + unprivileged user namespaces), which is not enabled on this host. '
            'Enable it (see docs/sandbox.md) or use the bash tool for shell tasks.'
        )
    return None


async def _ensure_session(ctx: RunContext[MarcelDeps]):
    """Return the turn's cell session, opening a sandboxed one on first use."""
    from marcel_core.config import settings
    from marcel_core.harness.code_exec.session import open_sandboxed_cell_session

    turn = ctx.deps.turn
    if turn.cell_session is not None:
        return turn.cell_session

    workspace = Path(ctx.deps.cwd) if ctx.deps.cwd else _PROJECT_ROOT
    session = await open_sandboxed_cell_session(
        workspace=workspace,
        cwd=workspace,
        data_dir=settings.data_dir,
        user_slug=ctx.deps.user_slug,
        timeout=settings.marcel_code_exec_timeout_seconds,
    )
    turn.cell_session = session
    return session


def _format_reply(reply) -> str:
    """Render a CellReply for the model: stdout, then echoed value or error."""
    parts: list[str] = []
    if reply.stdout:
        parts.append(reply.stdout.rstrip('\n'))
    if reply.ok:
        if reply.value_repr is not None:
            parts.append(reply.value_repr)
        text = '\n'.join(parts) if parts else '(no output)'
    else:
        parts.append(reply.error or 'cell failed')
        text = '\n'.join(parts)
    if len(text) > _MAX_OUTPUT:
        text = text[:_MAX_OUTPUT] + f'\n\n[Output truncated: {len(text)} chars total]'
    return text


async def code_exec(ctx: RunContext[MarcelDeps], code: str) -> str:
    """Run a Python cell in a persistent, sandboxed notebook.

    Cells share one namespace within a turn — names bound in one cell are
    visible in the next, like a REPL. Call Marcel toolkits from inside a cell
    with ``toolkit("family.action", **params)``; a trailing bare expression is
    echoed like a REPL (its ``repr`` is returned). Prototype a computation
    here, then use ``promote_extension`` to make a proven script a durable tool.

    Args:
        ctx: Agent context.
        code: The Python cell to execute.

    Returns:
        The cell's captured stdout plus its echoed value, or — on failure — the
        error traceback. A clear refusal string when the sandbox is unavailable.
    """
    reason = _unavailable_reason()
    if reason is not None:
        return reason

    log.info('[code_exec] user=%s bytes=%d', ctx.deps.user_slug, len(code))
    try:
        session = await _ensure_session(ctx)
        reply = await session.run_cell(code)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the model, never crash the turn
        log.exception('[code_exec] session/exec failure')
        return f'code_exec failed: {exc}'
    return _format_reply(reply)
