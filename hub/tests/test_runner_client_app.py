"""The runner client wired into the app: settings, `/api/health` and the start-up drain."""

import json
import socketserver
import tempfile
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marcel_hub.app import Settings, create_app, load_settings
from marcel_hub.runner_client import DEFAULT_SOCKET, RunnerClient
from marcel_hub.tasks.ports import NoRunner


def test_settings_point_at_the_mounted_socket_by_default(tmp_path: Path) -> None:
    s = load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml')})
    assert s.runner_sock == DEFAULT_SOCKET == Path('/run/marcel/runner.sock')
    assert s.worker_cwd is None


def test_settings_read_the_runner_socket_and_worker_workspace(tmp_path: Path) -> None:
    cfg = tmp_path / 'marcel.toml'
    cfg.write_text('runner_sock = "/r.sock"\nworker_cwd = "/home/o/scratch"\n')
    s = load_settings({'MARCEL_CONFIG': str(cfg)})
    assert (s.runner_sock, s.worker_cwd) == (Path('/r.sock'), '/home/o/scratch')
    s = load_settings(
        {'MARCEL_CONFIG': str(cfg), 'MARCEL_RUNNER_SOCK': '/e.sock', 'MARCEL_WORKER_CWD': '/e'}
    )
    assert (s.runner_sock, s.worker_cwd) == (Path('/e.sock'), '/e')


def test_without_a_socket_the_hub_runs_with_no_runner(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        runner = client.get('/api/health').json()['runner']
    assert isinstance(app.state.tasks.runner, NoRunner)
    assert runner == {
        'reachable': False,
        'reason': 'The hub has no runner set up, so it cannot see the NUC.',
    }


@pytest.fixture
def short_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix='mrc', dir='/tmp'))  # socket paths must stay short
    yield path
    for child in path.iterdir():
        child.unlink()
    path.rmdir()


def settings(tmp_path: Path, sock: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, db_path=tmp_path / 'marcel.db', runner_sock=sock, worker_cwd='/w'
    )


def wait_for_health(client: TestClient, reachable: bool) -> dict:
    deadline = time.monotonic() + 5
    while True:
        body = client.get('/api/health').json()['runner']
        if body['reachable'] is reachable or time.monotonic() > deadline:
            return body
        time.sleep(0.02)


def test_an_absent_runner_shows_in_health_with_a_reason(tmp_path: Path, short_dir: Path) -> None:
    app = create_app(settings(tmp_path, short_dir / 'runner.sock'))
    with TestClient(app) as client:
        runner = wait_for_health(client, reachable=False)
        assert app.state.runner_watch.reachable is False
    assert isinstance(app.state.tasks.runner, RunnerClient)
    assert runner['reachable'] is False
    assert 'not answering' in runner['reason']


class FakeRunnerHandler(BaseHTTPRequestHandler):
    """A runner on a real unix socket that answers `/events` with a stream that stays open."""

    stop = threading.Event()

    def address_string(self) -> str:
        return 'unix'

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        if self.path != '/events':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.end_headers()
        event = {'seq': 1, 'type': 'runner.warning', 'at': '2026-10-04T09:05:02Z', 'message': 'Hi.'}
        self.wfile.write(f'id: 1\ndata: {json.dumps(event)}\n\n'.encode())
        self.wfile.flush()
        while not self.stop.wait(0.02):
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def test_a_reachable_runner_shows_in_health(tmp_path: Path, short_dir: Path) -> None:
    sock = short_dir / 'runner.sock'
    FakeRunnerHandler.stop.clear()
    server = Server(str(sock), FakeRunnerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = create_app(settings(tmp_path, sock))
        with TestClient(app) as client:
            assert wait_for_health(client, reachable=True) == {'reachable': True}
            FakeRunnerHandler.stop.set()
    finally:
        FakeRunnerHandler.stop.set()
        server.shutdown()
        server.server_close()


def test_health_shows_an_unexpected_watcher_error_then_the_recovery(
    tmp_path: Path, short_dir: Path
) -> None:
    sock = short_dir / 'runner.sock'
    FakeRunnerHandler.stop.clear()
    server = Server(str(sock), FakeRunnerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = create_app(settings(tmp_path, sock))
        drain = app.state.tasks.drain
        calls = []

        async def flaky_drain() -> None:
            calls.append(1)
            if len(calls) in (2, 3):  # the start-up drain passes; the watcher's first two fail
                raise RuntimeError('database is locked')
            await drain()

        app.state.tasks.drain = flaky_drain
        with TestClient(app) as client:
            deadline = time.monotonic() + 5
            while 'database is locked' not in client.get('/api/health').json()['runner'].get(
                'reason', ''
            ):
                assert time.monotonic() < deadline, 'health never showed the error'
                time.sleep(0.01)
            assert wait_for_health(client, reachable=True) == {'reachable': True}
            FakeRunnerHandler.stop.set()
    finally:
        FakeRunnerHandler.stop.set()
        server.shutdown()
        server.server_close()
