"""Sandbox-side worker for code_exec (F3.3).

Runs as ``python -m marcel_core.harness.code_exec.worker <user_slug>`` **inside**
the bubblewrap sandbox. It speaks the code_exec protocol over stdin/stdout:

    read  CellRequest   → run the cell in a persistent CellServer → write CellReply
    (during a cell) toolkit() → write ToolkitCall → block reading a ToolkitReply

Only protocol messages cross stdin/stdout — a cell's own ``print`` output is
captured by the :class:`~marcel_core.harness.code_exec.server.CellServer` and
returned *inside* the ``CellReply``, so it never corrupts the framed stream.
The loop ends on EOF (the kernel closed the session), which is how a per-turn
session is torn down.

The worker holds only untrusted cell code. Anything a cell reaches for through
``toolkit()`` is serviced back in the kernel (see
:func:`~marcel_core.harness.code_exec.bridge.serve_toolkit_call`) — the sandbox
never gets credentials, the DB, or the network.
"""

from __future__ import annotations

import sys
from typing import Callable, TextIO

from marcel_core.harness.code_exec.protocol import CellRequest, ToolkitCall, ToolkitReply, decode, encode
from marcel_core.harness.code_exec.server import CellServer


def _make_call_toolkit(stdin: TextIO, stdout: TextIO) -> Callable[[ToolkitCall], ToolkitReply]:
    """Build the CellServer's ``call_toolkit``: RPC a ToolkitCall to the kernel."""

    def call_toolkit(call: ToolkitCall) -> ToolkitReply:
        stdout.write(encode(call) + '\n')
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise EOFError('kernel closed the channel while awaiting a toolkit reply')
        reply = decode(line.strip())
        if not isinstance(reply, ToolkitReply):
            raise ValueError(f'expected a ToolkitReply, got {type(reply).__name__}')
        return reply

    return call_toolkit


def main(argv: list[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the worker loop until stdin reaches EOF. Returns a process exit code.

    ``argv`` is the process args after the module (``[user_slug]``); ``stdin`` /
    ``stdout`` default to the real streams but are injectable for in-process
    tests. Reads are line-based (``readline``) — never stream iteration — so the
    main loop and ``call_toolkit`` can share stdin without read-ahead buffering
    swallowing each other's messages.
    """
    args = sys.argv[1:] if argv is None else argv
    user_slug = args[0] if args else ''
    stream_in = stdin if stdin is not None else sys.stdin
    stream_out = stdout if stdout is not None else sys.stdout

    server = CellServer(_make_call_toolkit(stream_in, stream_out), user_slug=user_slug)
    while True:
        line = stream_in.readline()
        if not line:  # EOF — the kernel closed the session
            return 0
        line = line.strip()
        if not line:
            continue
        message = decode(line)
        if isinstance(message, CellRequest):
            reply = server.run_cell(message.code)
            stream_out.write(encode(reply) + '\n')
            stream_out.flush()
        # A ToolkitReply outside an active cell is unexpected; ignore it rather
        # than crash — the loop stays responsive to the next CellRequest.


if __name__ == '__main__':  # pragma: no cover - process entrypoint, exercised via subprocess
    raise SystemExit(main())
