"""The persistent-namespace cell executor — the co-work notebook engine (F3).

A :class:`CellServer` execs cells against a **single, persistent namespace**:
names bound in one cell are visible in the next, exactly like a Jupyter kernel
or a REPL. This is the "persistent cells (notebook)" state model chosen for F3
— the agent builds a computation up incrementally, proves it out, then (via
``promote_extension``) turns the proven script into a durable extension.

The server is **transport-agnostic and synchronous** so it is fully testable
in-process (F3.2). In production (F3.3) it runs inside the bubblewrap sandbox
worker; its one outward dependency — servicing ``toolkit()`` — is injected as a
``call_toolkit`` callable, which the worker implements as an RPC to the kernel
and tests implement directly.

Each cell's stdout/stderr, the ``repr`` of a trailing bare expression, and any
traceback are captured into a :class:`~marcel_core.harness.code_exec.protocol.CellReply`.
A cell can raise anything; the server reports it and stays alive.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import traceback
from typing import Any, Callable

from marcel_core.harness.code_exec.protocol import CellReply, ToolkitCall

# The server's one injected dependency: turn a toolkit() call into a reply.
CallToolkit = Callable[[ToolkitCall], 'Any']

_CELL_FILENAME = '<cell>'


class ToolkitError(RuntimeError):
    """Raised inside a cell when a ``toolkit()`` call fails.

    A missing handler or a handler that raised comes back as a normal Python
    exception the cell code can ``try/except`` — never a silent ``None`` and
    never a torn RPC channel.
    """


def _get_logger(name: str = 'cell') -> logging.Logger:
    """A stdlib logger for cells — output is captured with the cell's stdout.

    Deliberately *not* the kernel-backed ``marcel_sdk.get_logger``: cells run
    in the lean sandbox worker, so a plain ``logging`` logger keeps that
    process free of a kernel import just to log a line.
    """
    return logging.getLogger(f'marcel.cell.{name}')


class CellServer:
    """Execute notebook cells against a persistent namespace.

    Args:
        call_toolkit: Services a cell's ``toolkit()`` request. In-process this
            drives the kernel handler directly (see
            :func:`marcel_core.harness.code_exec.bridge.make_local_call_toolkit`);
            in the sandbox it round-trips a
            :class:`~marcel_core.harness.code_exec.protocol.ToolkitCall` over
            the worker pipe.
        user_slug: The acting user, exposed to cells as ``user_slug`` and
            forwarded with every toolkit call.
    """

    def __init__(self, call_toolkit: CallToolkit, *, user_slug: str = '') -> None:
        self._call_toolkit = call_toolkit
        self._user_slug = user_slug
        # One namespace, reused for every cell — this is what makes it a notebook.
        self._ns: dict[str, Any] = {'__name__': '__cell__'}
        self._ns['toolkit'] = self._toolkit
        self._ns['get_logger'] = _get_logger
        self._ns['user_slug'] = user_slug
        # Injected so cells can ``except ToolkitError`` a failed toolkit() call.
        self._ns['ToolkitError'] = ToolkitError

    def _toolkit(self, tool_id: str, /, **params: Any) -> str:
        """The ``toolkit()`` injected into every cell.

        Calls a kernel-side toolkit handler and returns its string result.
        Raises :class:`ToolkitError` if the handler is missing or failed.
        """
        reply = self._call_toolkit(ToolkitCall(tool_id=tool_id, params=dict(params)))
        if not reply.ok:
            raise ToolkitError(reply.error or f'toolkit call {tool_id!r} failed')
        return reply.value or ''

    def run_cell(self, code: str) -> CellReply:
        """Execute one cell; never raises — failures come back as a CellReply.

        A trailing bare expression is split off and ``eval``'d so its value is
        echoed (REPL-style) in ``value_repr``. Everything printed to stdout or
        stderr during the cell is captured. On any exception the traceback is
        trimmed to the cell's own frames and returned as ``error``.
        """
        try:
            module = ast.parse(code, _CELL_FILENAME, 'exec')
        except SyntaxError as exc:
            return CellReply(ok=False, error=''.join(traceback.format_exception_only(type(exc), exc)).rstrip())

        last_expr: ast.Expression | None = None
        if module.body and isinstance(module.body[-1], ast.Expr):
            trailing = module.body.pop()
            last_expr = ast.Expression(trailing.value)  # type: ignore[attr-defined]
            ast.copy_location(last_expr, trailing)

        out = io.StringIO()
        value_repr: str | None = None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                if module.body:
                    exec(compile(module, _CELL_FILENAME, 'exec'), self._ns)  # noqa: S102 — sandboxed cell exec
                if last_expr is not None:
                    value = eval(compile(last_expr, _CELL_FILENAME, 'eval'), self._ns)  # noqa: S307
                    if value is not None:
                        value_repr = repr(value)
        except BaseException as exc:  # noqa: BLE001 — a cell may raise anything; report it, keep the server alive
            return CellReply(ok=False, stdout=out.getvalue(), error=_format_cell_traceback(exc))
        return CellReply(ok=True, stdout=out.getvalue(), value_repr=value_repr)


def _format_cell_traceback(exc: BaseException) -> str:
    """Format *exc* showing only the cell's own frames, not the exec machinery.

    The traceback starts at the first frame executing ``<cell>`` code, so the
    agent reading the error sees its cell line, not ``run_cell``/``exec``.
    """
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != _CELL_FILENAME:
        tb = tb.tb_next
    return ''.join(traceback.format_exception(type(exc), exc, tb)).rstrip()
