"""Per-habitat UDS bridge — runs *inside* a habitat subprocess.

Phase 1 of ISSUE-f60b09. The kernel spawns one instance of this module
per habitat declaring ``isolation: uds`` in its ``integration.yaml``;
the bridge:

1. Loads the habitat's ``__init__.py`` via ``importlib.util``. The
   habitat's ``@register`` calls populate the *bridge's own*
   ``_registry`` — same module path as the kernel uses, but a separate
   dict because this is a separate Python process.
2. Starts a ``asyncio.start_unix_server`` on a socket file created with
   mode 0600 (user-only). Stale socket files from a prior unclean exit
   are unlinked before bind.
3. Accepts connections in parallel; each client sends length-prefixed
   JSON-RPC 2.0 messages, the bridge dispatches to the registered async
   handler and replies on the same connection.
4. Shuts the server down cleanly on SIGTERM so the kernel's lifespan
   teardown doesn't have to escalate to SIGKILL.

Wire format (both directions)::

    [4-byte BE length][JSON body]

Request body::

    {"jsonrpc": "2.0", "id": <any>, "method": "<family>.<action>",
     "params": {"params": {...}, "user_slug": "<slug>"}}

Response body (success)::

    {"jsonrpc": "2.0", "id": <echoed>, "result": "<handler return>"}

Response body (error)::

    {"jsonrpc": "2.0", "id": <echoed>, "error": {"code": -32601 | -32000 | -32700,
                                                  "message": "..."}}

Error codes follow JSON-RPC 2.0:

- ``-32700`` Parse error — malformed JSON or framing
- ``-32601`` Method not found — handler name not registered
- ``-32000`` Server error — handler raised an exception

Not a public API. The kernel's ``_make_uds_proxy`` speaks this wire
format; habitat authors never touch it directly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import signal
import struct
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_LEN_PREFIX = 4  # bytes, big-endian
_MAX_FRAME = 8 * 1024 * 1024  # 8 MiB — defensive cap on any single RPC payload


def _load_habitat(habitat_dir: Path) -> None:
    """Import the habitat package so its ``@register`` calls fire.

    The module goes into ``sys.modules`` under a private namespace
    matching the kernel's ``_marcel_ext_integrations`` convention so
    intra-habitat relative imports work.
    """
    init_py = habitat_dir / '__init__.py'
    if not init_py.exists():
        raise RuntimeError(f'Habitat at {habitat_dir} is missing __init__.py')

    module_name = f'_marcel_ext_integrations.{habitat_dir.name}'
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_py,
        submodule_search_locations=[str(habitat_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Could not create module spec for habitat {habitat_dir}')

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


async def _read_frame(reader: asyncio.StreamReader) -> bytes | None:
    """Read one length-prefixed frame from *reader*.

    Returns ``None`` on clean EOF (peer closed the connection), raising
    only on malformed framing.
    """
    try:
        hdr = await reader.readexactly(_LEN_PREFIX)
    except asyncio.IncompleteReadError:
        return None
    (length,) = struct.unpack('>I', hdr)
    if length > _MAX_FRAME:
        raise ValueError(f'frame too large: {length} bytes (cap {_MAX_FRAME})')
    return await reader.readexactly(length)


def _write_frame(writer: asyncio.StreamWriter, body: bytes) -> None:
    """Write one length-prefixed frame to *writer*. Caller awaits drain."""
    writer.write(struct.pack('>I', len(body)) + body)


def _error_response(request_id: object, code: int, message: str) -> bytes:
    return json.dumps(
        {
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {'code': code, 'message': message},
        }
    ).encode()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle one client connection — loop reading frames until EOF.

    A single connection can carry multiple request/response pairs; the
    kernel's Phase-1 proxy opens one connection per call, but leaving
    the loop in place makes a future pooling optimization trivial.
    """
    # Import lazily so tests that only exercise the framing layer don't
    # need the habitat loaded.
    from marcel_core.toolkit import get_handler

    try:
        while True:
            frame = await _read_frame(reader)
            if frame is None:
                return  # clean EOF

            request_id: object = None
            try:
                req = json.loads(frame)
                request_id = req.get('id')
                method = req['method']
                payload = req.get('params') or {}
                params = payload.get('params') or {}
                user_slug = payload.get('user_slug', '')
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                _write_frame(writer, _error_response(request_id, -32700, f'parse error: {exc}'))
                await writer.drain()
                continue

            try:
                handler = get_handler(method)
            except KeyError:
                _write_frame(writer, _error_response(request_id, -32601, f'method not found: {method!r}'))
                await writer.drain()
                continue

            try:
                result = await handler(params, user_slug)
            except Exception as exc:
                log.exception('handler %s raised', method)
                _write_frame(writer, _error_response(request_id, -32000, f'{type(exc).__name__}: {exc}'))
                await writer.drain()
                continue

            body = json.dumps({'jsonrpc': '2.0', 'id': request_id, 'result': result}).encode()
            _write_frame(writer, body)
            await writer.drain()
    except ConnectionResetError:
        return
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _serve(socket_path: Path) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)  # clean stale socket from a prior unclean exit

    # Tighten umask across the bind so the socket is born with mode 0600 —
    # closes the microsecond window between start_unix_server() creating
    # the file under the process default umask and our explicit chmod.
    # Restored immediately so habitat code that creates files later uses
    # the caller's umask, not ours.
    old_umask = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
        socket_path.chmod(0o600)  # belt-and-braces; umask already gave us 0600
    finally:
        os.umask(old_umask)

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _request_stop() -> None:
        if not stop.done():
            stop.set_result(None)

    loop.add_signal_handler(signal.SIGTERM, _request_stop)
    loop.add_signal_handler(signal.SIGINT, _request_stop)

    log.info('uds-bridge: listening on %s', socket_path)
    try:
        await stop
    finally:
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)
        log.info('uds-bridge: stopped')


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write('usage: python -m marcel_core.plugin._uds_bridge <habitat_dir> <socket_path>\n')
        return 2

    habitat_dir = Path(argv[1]).resolve()
    socket_path = Path(argv[2])

    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)-8s] [%(asctime)s] uds-bridge[%(name)s]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    _load_habitat(habitat_dir)
    asyncio.run(_serve(socket_path))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
