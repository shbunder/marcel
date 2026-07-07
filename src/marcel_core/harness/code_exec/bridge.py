"""Kernel side of the code_exec toolkit RPC (F3).

A cell calls ``toolkit("family.action", **params)``. In the sandbox that
becomes a :class:`~marcel_core.harness.code_exec.protocol.ToolkitCall` sent to
the kernel; here is where the kernel answers it. :func:`serve_toolkit_call`
resolves the handler in the in-process toolkit registry, runs it, and returns
a serialisable :class:`~marcel_core.harness.code_exec.protocol.ToolkitReply`.

The registry and every toolkit handler live in the **kernel**, never in the
sandboxed worker — that is the whole point of the RPC. The worker holds only
untrusted cell code; anything that touches credentials, the DB, or the network
runs here, behind the handler the operator installed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

from marcel_core.harness.code_exec.protocol import ToolkitCall, ToolkitReply

log = logging.getLogger(__name__)


async def serve_toolkit_call(call: ToolkitCall, user_slug: str) -> ToolkitReply:
    """Run a cell's ``toolkit()`` request against the kernel registry.

    Never raises: a missing handler or a handler that throws becomes a
    ``ToolkitReply(ok=False, error=...)`` so the cell sees a clean
    :class:`~marcel_core.harness.code_exec.server.ToolkitError` and the RPC
    loop keeps running.
    """
    from marcel_core.toolkit import get_handler

    try:
        handler = get_handler(call.tool_id)
    except KeyError as exc:
        return ToolkitReply(ok=False, error=str(exc))
    try:
        value = await handler(dict(call.params), user_slug)
    except Exception as exc:  # noqa: BLE001 — surface the handler failure to the cell, don't kill the loop
        log.warning('toolkit call %r failed: %s', call.tool_id, exc)
        return ToolkitReply(ok=False, error=f'{type(exc).__name__}: {exc}')
    return ToolkitReply(ok=True, value=str(value))


def make_local_call_toolkit(user_slug: str) -> Callable[[ToolkitCall], ToolkitReply]:
    """A synchronous ``call_toolkit`` that drives the async kernel handler.

    Used in-process — by the F3.2 tests, and by any non-sandboxed driver of a
    :class:`~marcel_core.harness.code_exec.server.CellServer`. Each handler
    coroutine runs to completion on a private event loop in a worker thread,
    so this is safe to call whether or not the caller already owns a running
    loop (the sandboxed production path uses pipe RPC instead and never touches
    this).
    """

    def call_toolkit(tk: ToolkitCall) -> ToolkitReply:
        return _run_coro_blocking(serve_toolkit_call(tk, user_slug))

    return call_toolkit


def _run_coro_blocking(coro: Any) -> ToolkitReply:
    """Run *coro* to completion on a fresh loop in a dedicated thread."""
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box['result'] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the calling thread below
            box['error'] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if 'error' in box:
        raise box['error']
    return box['result']
