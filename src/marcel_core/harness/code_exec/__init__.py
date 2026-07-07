"""code_exec — the co-work notebook engine (F3).

The agent runs Python **cells** against a persistent namespace, calls Marcel's
toolkits from inside them via ``toolkit()``, proves a computation out, then
promotes the proven script into a durable extension
(:mod:`marcel_core.tools.promote`).

Layers:

- :mod:`.protocol` — the four wire messages (CellRequest/Reply, ToolkitCall/Reply).
- :mod:`.server`   — :class:`.server.CellServer`, the persistent-namespace executor
  that runs inside the sandbox worker (transport-agnostic, testable in-process).
- :mod:`.bridge`   — the kernel side of the toolkit RPC (:func:`.bridge.serve_toolkit_call`).

F3.3 adds the sandboxed worker process and the ``code_exec`` tool that drives it.
"""

from __future__ import annotations

from marcel_core.harness.code_exec.bridge import make_local_call_toolkit, serve_toolkit_call
from marcel_core.harness.code_exec.protocol import (
    CellReply,
    CellRequest,
    ToolkitCall,
    ToolkitReply,
    decode,
    encode,
)
from marcel_core.harness.code_exec.server import CellServer, ToolkitError

__all__ = [
    'CellReply',
    'CellRequest',
    'CellServer',
    'ToolkitCall',
    'ToolkitError',
    'ToolkitReply',
    'decode',
    'encode',
    'make_local_call_toolkit',
    'serve_toolkit_call',
]
