"""In-process tests for the sandbox worker loop (F3.3).

The session tests spawn the worker as a real subprocess (coverage can't see
across the process boundary); these drive ``worker.main`` directly over string
buffers, so the loop, framing, and toolkit ping-pong are covered in-process.
A preloaded stdin works because the protocol is strict request/response: for a
cell that makes one toolkit call, stdin holds the CellRequest followed by the
ToolkitReply the worker will read back.
"""

from __future__ import annotations

import io

from marcel_core.harness.code_exec import worker
from marcel_core.harness.code_exec.protocol import (
    CellReply,
    CellRequest,
    ToolkitCall,
    ToolkitReply,
    decode,
    encode,
)


def _run(lines: list[str], *, user_slug: str = 'alice') -> list:
    """Feed *lines* to the worker as stdin; return decoded stdout messages."""
    stdin = io.StringIO('\n'.join(lines) + '\n' if lines else '')
    stdout = io.StringIO()
    rc = worker.main([user_slug], stdin=stdin, stdout=stdout)
    assert rc == 0
    out = stdout.getvalue().splitlines()
    return [decode(line) for line in out if line.strip()]


def test_worker_runs_a_simple_cell():
    msgs = _run([encode(CellRequest(code='40 + 2'))])
    assert msgs == [CellReply(ok=True, value_repr='42')]


def test_worker_persists_namespace_across_cells():
    msgs = _run([encode(CellRequest(code='n = 7')), encode(CellRequest(code='n * 6'))])
    assert msgs[-1] == CellReply(ok=True, value_repr='42')


def test_worker_services_a_toolkit_call():
    # The cell calls toolkit(); the worker emits a ToolkitCall and reads the
    # preloaded ToolkitReply, then returns the CellReply.
    lines = [
        encode(CellRequest(code='toolkit("greet.hi", who="x")')),
        encode(ToolkitReply(ok=True, value='hello')),
    ]
    msgs = _run(lines)
    assert msgs[0] == ToolkitCall(tool_id='greet.hi', params={'who': 'x'})
    assert msgs[1] == CellReply(ok=True, value_repr="'hello'")


def test_worker_reports_a_cell_error():
    msgs = _run([encode(CellRequest(code='raise ValueError("boom")'))])
    assert len(msgs) == 1
    assert not msgs[0].ok
    assert 'ValueError: boom' in msgs[0].error


def test_worker_exposes_user_slug():
    msgs = _run([encode(CellRequest(code='user_slug'))], user_slug='bob')
    assert msgs[0] == CellReply(ok=True, value_repr="'bob'")


def test_worker_skips_blank_lines_and_stops_on_eof():
    # Blank line between two cells; EOF ends the loop cleanly.
    msgs = _run(['', encode(CellRequest(code='1'))])
    assert msgs == [CellReply(ok=True, value_repr='1')]


def test_worker_ignores_a_stray_toolkit_reply():
    # A ToolkitReply with no active cell is ignored, not fatal.
    msgs = _run([encode(ToolkitReply(ok=True, value='x')), encode(CellRequest(code='2'))])
    assert msgs == [CellReply(ok=True, value_repr='2')]


def test_worker_toolkit_eof_surfaces_as_cell_error():
    # Cell calls toolkit() but the kernel never sends a reply (EOF): the cell's
    # run_cell catches the EOFError and returns a failed CellReply.
    msgs = _run([encode(CellRequest(code='toolkit("greet.hi")'))])
    assert msgs[0] == ToolkitCall(tool_id='greet.hi', params={})
    assert not msgs[1].ok
    assert 'EOFError' in msgs[1].error


def test_worker_toolkit_wrong_reply_type_surfaces_as_cell_error():
    # A non-ToolkitReply where a reply is expected is a protocol error the cell
    # sees as a failure, not a crash of the worker.
    lines = [
        encode(CellRequest(code='toolkit("greet.hi")')),
        encode(CellReply(ok=True, value_repr='oops')),  # wrong type as the "reply"
    ]
    msgs = _run(lines)
    assert not msgs[1].ok
    assert 'ToolkitReply' in msgs[1].error
