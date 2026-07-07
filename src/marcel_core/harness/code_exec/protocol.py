"""Wire protocol for the code_exec co-work notebook (F3).

Four message types cross the kernel↔worker boundary. The worker runs the
:class:`~marcel_core.harness.code_exec.server.CellServer` (inside the F3.3
sandbox); the kernel drives it and services the toolkit RPC:

    kernel → worker :  CellRequest   (run this cell)
    worker → kernel :  ToolkitCall   (a cell called toolkit(); please service it)
    kernel → worker :  ToolkitReply  (result of that toolkit call)
    worker → kernel :  CellReply     (the cell finished — stdout / value / error)

All four are plain dataclasses that round-trip through a single tagged,
newline-framed JSON stream (:func:`encode` / :func:`decode`). Keeping the
protocol here — with no transport — is what lets F3.2 test the whole path
in-process and lets F3.3 drop in real subprocess pipes without touching the
server or the bridge.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CellRequest:
    """kernel → worker: execute *code* in the persistent namespace."""

    code: str


@dataclass
class CellReply:
    """worker → kernel: the outcome of one cell.

    ``value_repr`` echoes the ``repr()`` of a trailing bare expression
    (REPL-style), or ``None`` when the cell ends in a statement. ``error``
    carries a human-readable traceback when ``ok`` is ``False``.
    """

    ok: bool
    stdout: str = ''
    value_repr: str | None = None
    error: str | None = None


@dataclass
class ToolkitCall:
    """worker → kernel: a cell invoked ``toolkit(tool_id, **params)``."""

    tool_id: str
    params: dict = field(default_factory=dict)


@dataclass
class ToolkitReply:
    """kernel → worker: the result of servicing a :class:`ToolkitCall`.

    ``ok=False`` (missing handler / handler raised) surfaces inside the cell
    as a :class:`~marcel_core.harness.code_exec.server.ToolkitError`, never a
    broken channel.
    """

    ok: bool
    value: str | None = None
    error: str | None = None


_TYPES: dict[str, type] = {c.__name__: c for c in (CellRequest, CellReply, ToolkitCall, ToolkitReply)}

# A single message union — every message on the stream is one of these.
Message = CellRequest | CellReply | ToolkitCall | ToolkitReply


def encode(msg: Message) -> str:
    """Serialise *msg* to one tagged JSON line (no trailing newline)."""
    return json.dumps({'type': type(msg).__name__, 'data': asdict(msg)}, separators=(',', ':'))


def decode(line: str) -> Message:
    """Parse one tagged JSON line back into its dataclass.

    Raises:
        ValueError: if the line is not valid JSON, lacks a ``type`` tag, or
            names a type this protocol does not define.
    """
    try:
        obj: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f'not a protocol message (invalid JSON): {exc}') from exc
    tag = obj.get('type')
    cls = _TYPES.get(tag) if isinstance(tag, str) else None
    if cls is None:
        raise ValueError(f'unknown protocol message type: {tag!r}')
    return cls(**obj.get('data', {}))
