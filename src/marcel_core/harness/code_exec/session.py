"""Kernel-side driver for a code_exec worker subprocess (F3.3).

A :class:`CellSession` owns one worker process and pumps the code_exec
protocol against it: it sends a cell, services any ``toolkit()`` RPC the cell
makes (in the kernel, via :func:`serve_toolkit_call`), and returns the
worker's :class:`CellReply`. The worker holds the persistent namespace, so a
session reused across cells IS the notebook — created lazily on the turn's
first ``code_exec`` and closed at turn end.

The subprocess spawn is deliberately factored out (:func:`open_cell_session`
takes an argv): production wraps the worker in the bubblewrap sandbox
(:func:`open_sandboxed_cell_session`), while tests spawn the same worker as a
plain subprocess (:func:`worker_argv`) — so the pump, the RPC servicing, the
timeout, and teardown are all testable without user namespaces.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import sys
from pathlib import Path

from marcel_core.harness.code_exec.bridge import serve_toolkit_call
from marcel_core.harness.code_exec.protocol import CellReply, CellRequest, Message, ToolkitCall, decode, encode

log = logging.getLogger(__name__)

_CLOSE_GRACE_SECONDS = 5.0
# Framed messages (notably a CellReply carrying a cell's stdout) can exceed the
# asyncio default readline limit of 64 KiB; raise it so a chatty cell does not
# blow up the reader. The tool truncates output for display separately.
_STREAM_LIMIT = 8 * 1024 * 1024


class CellSessionError(RuntimeError):
    """The worker died, misbehaved, or the session was used after close."""


class CellSession:
    """Drive one code_exec worker subprocess over the framed protocol.

    Args:
        proc: A started subprocess with ``stdin``/``stdout`` pipes running the
            code_exec worker (sandboxed or not — the session does not care).
        user_slug: Forwarded to :func:`serve_toolkit_call` for every toolkit
            request the cell makes — the authority is the kernel's, not the
            sandbox's.
        timeout: Per-cell wall-clock limit. A cell that exceeds it is
            terminated and the session is closed.
    """

    def __init__(self, proc: asyncio.subprocess.Process, *, user_slug: str, timeout: float) -> None:
        self._proc = proc
        self._user_slug = user_slug
        self._timeout = timeout
        self._closed = False

    async def run_cell(self, code: str) -> CellReply:
        """Run one cell; return its :class:`CellReply`.

        A runaway cell (one that outruns ``timeout``) is not left hanging: the
        worker is killed, the session closed, and an error CellReply returned —
        the agent sees a clean "timed out" rather than a stalled turn.
        """
        if self._closed or self._proc.returncode is not None:
            raise CellSessionError('cell session is closed')
        try:
            return await asyncio.wait_for(self._pump(code), timeout=self._timeout)
        except asyncio.TimeoutError:
            await self.close()
            return CellReply(ok=False, error=f'cell exceeded the {self._timeout:g}s time limit and was terminated')

    async def _pump(self, code: str) -> CellReply:
        """Send the cell and service messages until the worker returns a CellReply."""
        self._send(CellRequest(code=code))
        assert self._proc.stdin is not None and self._proc.stdout is not None
        await self._proc.stdin.drain()
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise CellSessionError('worker exited before returning a cell result')
            message = decode(line.decode())
            if isinstance(message, ToolkitCall):
                reply = await serve_toolkit_call(message, self._user_slug)
                self._send(reply)
                await self._proc.stdin.drain()
            elif isinstance(message, CellReply):
                return message
            else:
                raise CellSessionError(f'unexpected message from worker: {type(message).__name__}')

    def _send(self, message: Message) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write((encode(message) + '\n').encode())

    async def close(self) -> None:
        """Close the session and reap the worker. Idempotent."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc.returncode is not None:
            return
        # Closing stdin sends EOF; the worker loop exits cleanly on it.
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_CLOSE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()


def worker_argv() -> list[str]:
    """The command that runs the worker with the current interpreter."""
    return [sys.executable, '-m', 'marcel_core.harness.code_exec.worker']


async def open_cell_session(*, argv: list[str], user_slug: str, timeout: float) -> CellSession:
    """Spawn *argv* as a worker subprocess and wrap it in a :class:`CellSession`.

    ``argv`` must launch the code_exec worker (optionally behind a sandbox
    wrapper) and end with the worker module + ``user_slug``. stderr is captured
    so a worker that dies on startup can be diagnosed rather than spamming the
    kernel's own stderr.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        limit=_STREAM_LIMIT,
    )
    return CellSession(proc, user_slug=user_slug, timeout=timeout)


async def open_sandboxed_cell_session(
    *,
    workspace: Path,
    cwd: Path,
    data_dir: Path,
    user_slug: str,
    timeout: float,
) -> CellSession:
    """Open a session whose worker runs inside the bubblewrap sandbox.

    Network is disabled (``allow_network=False``) — the untrusted cell reaches
    the outside world only through ``toolkit()``, which is serviced in the
    kernel. Callers must check
    :func:`~marcel_core.harness.sandbox.sandbox_available` first; this builds a
    bwrap argv unconditionally.
    """
    from marcel_core.harness.sandbox import build_bwrap_argv

    argv = build_bwrap_argv(workspace=workspace, cwd=cwd, data_dir=data_dir, allow_network=False)
    argv += [*worker_argv(), user_slug]
    return await open_cell_session(argv=argv, user_slug=user_slug, timeout=timeout)
