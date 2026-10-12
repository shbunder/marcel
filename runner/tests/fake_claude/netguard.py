"""Make the fake `claude` process refuse every outbound connection.

`bin/claude` calls `forbid()` before anything else runs, so the shim, its players and anything
they import cannot reach Claude or the network, even by accident. It is the one module here that
imports `socket`, and only to switch it off. Never call it from a test process: it is permanent.
"""

from __future__ import annotations

import socket
from typing import Any, NoReturn

MESSAGE = 'fake claude: no network. The fake must never reach Claude or the internet.'


def _refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise ConnectionRefusedError(MESSAGE)


def forbid() -> None:
    socket.socket.connect = _refuse  # type: ignore[method-assign]
    socket.socket.connect_ex = _refuse  # type: ignore[method-assign]
    socket.create_connection = _refuse  # type: ignore[assignment]
    socket.getaddrinfo = _refuse  # type: ignore[assignment]
