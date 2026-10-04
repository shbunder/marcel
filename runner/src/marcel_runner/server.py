"""The runner's HTTP server on a unix socket. Today it serves `GET /health`."""

import grp
import json
import os
import socket
import socketserver
import stat
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from marcel_runner.health import SUPERVISOR_UNIT, Settings, cgroup_of, collect_health

SOCKET_MODE = 0o660
DIR_MODE = 0o750
DEFAULT_CAP = 4


class StartupError(Exception):
    """The runner will not start. The message says why and what to do, in plain words."""


def bind_socket(path: Path, group: str | None = None) -> socket.socket:
    """Bind `path` as a unix socket, mode 0660. Only a dead socket is ever deleted."""
    gid = None
    if group is not None:
        try:
            gid = grp.getgrnam(group).gr_gid
        except KeyError:
            raise StartupError(
                f'The group "{group}" does not exist, so the socket cannot be shared with it. '
                'Create it, or unset MARCEL_RUNNER_GROUP.'
            ) from None
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    if path.is_socket():
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(str(path))
        except OSError:
            path.unlink()  # nobody is listening: a leftover from a crash
        else:
            raise StartupError(
                f'A runner is already running on {path}. Stop it first, or use another socket.'
            )
        finally:
            probe.close()
    elif path.exists():
        raise StartupError(f'{path} exists and is not a socket, so the runner left it alone.')
    s = socket.socket(socket.AF_UNIX)
    old = os.umask(0o777 & ~SOCKET_MODE)
    try:
        s.bind(str(path))
    finally:
        os.umask(old)
    try:
        os.chmod(path, SOCKET_MODE)
        if gid is not None:
            os.chown(path, -1, gid)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != SOCKET_MODE:
            raise StartupError(
                f'The socket {path} has mode {mode:04o}, not 0660. '
                'The runner will not listen where others could reach it.'
            )
    except (StartupError, OSError) as e:
        s.close()
        path.unlink(missing_ok=True)
        if isinstance(e, StartupError):
            raise
        raise StartupError(f'Could not set up the socket {path}: {e.strerror or e}.') from e
    return s


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, sock: socket.socket, health: Callable[[], dict]) -> None:
        # bind_socket() has already bound `sock`; skip UnixStreamServer's own bind.
        socketserver.BaseServer.__init__(self, sock.getsockname(), Handler)
        self.socket = sock
        self.health = health
        sock.listen(16)


class Handler(BaseHTTPRequestHandler):
    server: UnixHTTPServer

    def address_string(self) -> str:  # unix sockets have no client address
        return 'unix'

    def log_message(self, format: str, *args) -> None:
        print(f'{self.command} {self.path} {format % args}', file=sys.stderr, flush=True)

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, error: str, message: str) -> None:
        self._send(code, {'code': error, 'message': message})

    def _dispatch(self) -> None:
        path = urlsplit(self.path).path
        if path != '/health':
            self._error(404, 'not_found', f'The runner has no {path}.')
        elif self.command != 'GET':
            self._error(405, 'method_not_allowed', f'/health only answers GET, not {self.command}.')
        else:
            self._send(200, self.server.health())

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch


def serve(
    path: Path,
    settings: Settings,
    group: str | None = None,
    cgroup_of: Callable[[int], str] = cgroup_of,
    active_workers: Callable[[], int] = lambda: 0,
) -> UnixHTTPServer:
    """Check the supervisor, bind the socket and return a server ready for `serve_forever()`."""

    def health() -> dict:
        return collect_health(settings, active_workers, cgroup_of)

    first = health()
    # With claude missing the supervisor cannot be checked: start anyway, /health says why.
    if first['claude_version'] and not (
        first['supervisor']['running'] and first['supervisor']['own_unit']
    ):
        raise StartupError(first['reason'] + f' The runner did not start. ({SUPERVISOR_UNIT})')
    return UnixHTTPServer(bind_socket(path, group), health)


def settings_from_env() -> tuple[Settings, Path, str | None]:
    env = os.environ
    try:
        cap = int(env.get('MARCEL_WORKER_CAP', DEFAULT_CAP))
    except ValueError:
        raise StartupError('MARCEL_WORKER_CAP must be a whole number, like 4.') from None
    claude = Path(env.get('MARCEL_CLAUDE') or Path.home() / '.local' / 'bin' / 'claude')
    runtime = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    sock = Path(env.get('MARCEL_RUNNER_SOCK') or Path(runtime) / 'marcel' / 'runner.sock')
    return Settings(claude=claude, cap=cap), sock, env.get('MARCEL_RUNNER_GROUP') or None


def main() -> int:
    try:
        settings, sock, group = settings_from_env()
        srv = serve(sock, settings, group)
    except StartupError as e:
        print(f'marcel-runner: {e}', file=sys.stderr)
        return 1
    print(f'marcel-runner: listening on {sock}', file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        sock.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
