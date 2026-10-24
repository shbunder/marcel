"""The runner's HTTP server on a unix socket: `GET /health`, `GET /usage`, `/sessions`
(with resume, fork and `repo` spawns), `GET /sessions/{id}/tail`, `GET /events` and
`/cloud/sessions` (cloud spawn and send)."""

import grp
import json
import os
import socket
import socketserver
import stat
import sys
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from marcel_runner import fork, resume
from marcel_runner.brains import Brains, brains_from_env
from marcel_runner.cloud import Cloud
from marcel_runner.health import Settings, check, version_problem
from marcel_runner.login import LoginCheck
from marcel_runner.platform import System, current
from marcel_runner.sessions import SessionError, Sessions, StateFileError, state_dir_from_env
from marcel_runner.transcript import Tails
from marcel_runner.usage import UsageProbe
from marcel_runner.watch import Watcher
from marcel_runner.workspaces import Workspaces, root_from_env

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
    except OSError as e:
        s.close()
        # macOS allows 104 bytes in a socket path, Linux 108: a deep MARCEL_HOME passes both.
        raise StartupError(
            f'The runner could not listen on {path}: {e.strerror or e}. '
            'Use a shorter path (MARCEL_RUNNER_SOCK, or a shorter MARCEL_HOME).'
        ) from e
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

    def __init__(
        self,
        sock: socket.socket,
        health: Callable[[], dict],
        usage: UsageProbe,
        sessions: Sessions | None = None,
        watcher: Watcher | None = None,
        tails: Tails | None = None,
        workspaces: Workspaces | None = None,
        cloud: Cloud | None = None,
        brains: Brains | None = None,
    ) -> None:
        # bind_socket() has already bound `sock`; skip UnixStreamServer's own bind.
        socketserver.BaseServer.__init__(self, sock.getsockname(), Handler)
        self.socket = sock
        self.health = health
        self.usage = usage
        self.sessions = sessions
        self.watcher = watcher
        self.tails = tails
        self.workspaces = workspaces
        self.cloud = cloud
        self.brains = brains
        sock.listen(16)


class Handler(BaseHTTPRequestHandler):
    server: UnixHTTPServer

    def address_string(self) -> str:  # unix sockets have no client address
        return 'unix'

    def log_message(self, format: str, *args) -> None:
        print(f'{self.command} {self.path} {format % args}', file=sys.stderr, flush=True)

    def _send(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, error: str, message: str) -> None:
        self._send(code, {'code': error, 'message': message})

    def _dispatch(self) -> None:
        url = urlsplit(self.path)
        path = url.path
        sessions = self.server.sessions
        parts = path.strip('/').split('/')
        if self.server.tails is not None and len(parts) == 3 and parts[::2] == ['sessions', 'tail']:
            # A transcript stream, not a JSON answer, so it is not one of `Sessions.route`'s.
            if self.command != 'GET':
                self._error(405, 'method_not_allowed', f'{path} only answers GET.')
            else:  # holds the connection until the client leaves
                self.server.tails.handle(self, unquote(parts[1]), parse_qs(url.query))
        elif sessions is not None and (path == '/sessions' or path.startswith('/sessions/')):
            length = int(self.headers.get('content-length') or 0)
            body = self.rfile.read(length) if length > 0 else b''
            query = parse_qs(url.query)
            self._send(
                *route_sessions(sessions, self.server.workspaces, self.command, path, query, body)
            )
        elif self.server.cloud is not None and path.startswith('/cloud/'):
            # Cloud sessions are not in Claude's roster, so `Cloud` routes them on its own.
            length = int(self.headers.get('content-length') or 0)
            body = self.rfile.read(length) if length > 0 else b''
            self._send(*self.server.cloud.route(self.command, path, body))
        elif path == '/brains' and self.server.brains is not None:
            # One agent's brain folder, made on the host for the hub (S-17.7).
            length = int(self.headers.get('content-length') or 0)
            body = self.rfile.read(length) if length > 0 else b''
            self._send(*self.server.brains.route(self.command, body))
        elif path == '/events' and self.server.watcher is not None:
            if self.command != 'GET':
                self._error(405, 'method_not_allowed', f'{path} only answers GET.')
            else:
                self.server.watcher.stream(self)  # holds the connection until the client leaves
        elif path not in ('/health', '/usage'):
            self._error(404, 'not_found', f'The runner has no {path}.')
        elif self.command != 'GET':
            self._error(405, 'method_not_allowed', f'{path} only answers GET, not {self.command}.')
        elif path == '/usage':
            self._send(*self.server.usage.reading())
        else:
            self._send(200, self.server.health())

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch


def route_sessions(
    sessions: Sessions,
    workspaces: Workspaces | None,
    method: str,
    path: str,
    query: Mapping[str, list[str]],
    body: bytes,
) -> tuple[int, Any]:
    """Answer one request under `/sessions`. Resume, fork and spawns with a `repo` have their own
    modules (S-03.5); this only picks the module. Everything else is `Sessions.route`'s."""
    parts = [unquote(p) for p in path.strip('/').split('/')]
    try:
        if len(parts) == 3 and parts[2] in ('resume', 'fork'):
            if method != 'POST':
                return 405, {'code': 'method_not_allowed', 'message': f'{path} only answers POST.'}
            if parts[2] == 'resume':
                return 200, resume.resume(sessions, parts[1], body)
            return 201, fork.fork(sessions, parts[1], body)
        if workspaces is not None and parts == ['sessions'] and method == 'POST':
            spawned = workspaces.spawn(sessions, body)  # None: no `repo`, an ordinary spawn
            if spawned is not None:
                return 201, spawned
    except SessionError as e:
        return e.status, e.body()
    return sessions.route(method, path, query, body)


def serve(
    path: Path,
    settings: Settings,
    group: str | None = None,
    system: System | None = None,
    active_workers: Callable[[], int] = lambda: 0,
    usage: UsageProbe | None = None,
    sessions: Sessions | None = None,
    watcher: Watcher | None = None,
    tails: Tails | None = None,
    workspaces: Workspaces | None = None,
    cloud: Cloud | None = None,
    brains: Brains | None = None,
    login: LoginCheck | None = None,
) -> UnixHTTPServer:
    """Check the supervisor, bind the socket and return a server ready for `serve_forever()`."""
    if sessions is not None:
        active_workers = sessions.active_workers
        if cloud is not None and cloud.check_version is None:
            # A cloud start answers 422 `claude_version` too (it needs no supervisor).
            live = sessions
            cloud.check_version = lambda: live.refuse_version('Nothing was started.')

    probe = usage if usage is not None else UsageProbe(settings.claude)
    if login is not None:
        probe.login = login  # the cost view is a lost login too
        login.refresh_usage = lambda: threading.Thread(
            target=probe.refresh, name='usage-after-login', daemon=True
        ).start()

    if system is None:
        system = sessions.system if sessions is not None else current()

    def health() -> dict:
        return check(settings, active_workers, system, login)[0]

    first, supervisor_problem = check(settings, active_workers, system, login)
    # With claude missing the supervisor cannot be checked: start anyway, /health says why. On a
    # Mac no supervisor is not a problem (the first `claude --bg` starts one); one that would
    # die with the runner is.
    if first['claude_version'] and supervisor_problem is not None:
        raise StartupError(f'{first["reason"]} The runner did not start.')
    wrong = version_problem(first['claude_version'], settings.expected)
    if wrong:  # serve anyway, so the hub and the app can show it; every start answers 422
        print(f'marcel-runner: {wrong} No session will start until then.', file=sys.stderr)
    if sessions is not None:
        sessions.refresh_usage = probe.refresh  # a fresh usage reading before each worker spawn
    sock = bind_socket(path, group)
    return UnixHTTPServer(sock, health, probe, sessions, watcher, tails, workspaces, cloud, brains)


def settings_from_env(system: System | None = None) -> tuple[Settings, Path, str | None]:
    env = os.environ
    try:
        cap = int(env.get('MARCEL_WORKER_CAP', DEFAULT_CAP))
    except ValueError:
        raise StartupError('MARCEL_WORKER_CAP must be a whole number, like 4.') from None
    system = current() if system is None else system
    claude = Path(env.get('MARCEL_CLAUDE') or Path.home() / '.local' / 'bin' / 'claude')
    if not claude.is_absolute():
        raise StartupError(
            f'MARCEL_CLAUDE must be an absolute path, not "{claude}". '
            f'{system.service_manager} has no ~/.local/bin, so a bare name would not be found.'
        )
    sock = Path(env.get('MARCEL_RUNNER_SOCK') or system.default_socket(env))
    return Settings(claude=claude, cap=cap), sock, env.get('MARCEL_RUNNER_GROUP') or None


def main() -> int:
    try:
        settings, sock, group = settings_from_env()
        sessions = Sessions(settings, state_dir_from_env())
        watcher = Watcher(sessions)
        tails = Tails(sessions.roster, sessions.home)
        workspaces = Workspaces(root_from_env(), settings.claude, sessions.home)
        srv = serve(
            sock,
            settings,
            group,
            sessions=sessions,
            watcher=watcher,
            tails=tails,
            workspaces=workspaces,
            cloud=Cloud(settings.claude, sessions.home, workspaces=workspaces),
            brains=brains_from_env(settings.claude, sessions.home),
            login=LoginCheck(
                settings.claude, sessions.state_dir, sessions.home / '.claude' / 'daemon.log'
            ),
        )
    except (StartupError, StateFileError) as e:
        print(f'marcel-runner: {e}', file=sys.stderr)
        return 1
    print(f'marcel-runner: listening on {sock}', file=sys.stderr, flush=True)
    srv.usage.start()
    watcher.start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        tails.close()
        srv.server_close()
        sock.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
