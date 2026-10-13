"""Live-test support: a runner of our own, started like production, with the real `claude`.

They need the NUC: Claude's supervisor running in its own unit (`marcel-claude-daemon.service`), the
owner logged in, and a trusted folder with the marcel plugin installed (`make -C deploy install`
makes `~/marcel/workspaces/scratch`). Run by hand: `make -C runner test-live`.
"""

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[2] / 'src'
CWD = Path(os.environ.get('MARCEL_LIVE_CWD') or Path.home() / 'marcel' / 'workspaces' / 'scratch')
MODEL = os.environ.get('MARCEL_LIVE_MODEL', 'haiku')
NODE_BIN = Path(os.environ.get('MARCEL_LIVE_NODE_BIN') or Path.home() / 'marcel' / 'node' / 'bin')


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__('runner', timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self._path))


class Runner:
    """The live runner's socket, and the sessions a test started (stopped at the end)."""

    def __init__(self, sock: Path) -> None:
        self.sock = sock
        self.started: list[str] = []

    def raw(
        self, method: str, path: str, body: Any = None, timeout: float = 180
    ) -> tuple[int, str]:
        conn = UnixConnection(self.sock, timeout)
        try:
            data = None if body is None else json.dumps(body)
            headers = {} if body is None else {'Content-Type': 'application/json'}
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()

    def call(
        self, method: str, path: str, body: Any = None, timeout: float = 180
    ) -> tuple[int, Any]:
        status, text = self.raw(method, path, body, timeout)
        return status, json.loads(text) if text else None

    def spawn(self, name: str, prompt: str, **extra: Any) -> tuple[int, Any]:
        body = {'role': 'worker', 'name': name, 'model': MODEL, 'prompt': prompt, 'cwd': str(CWD)}
        status, out = self.call('POST', '/sessions', {**body, **extra})
        if status == 201:
            self.started.append(out['session_id'])
        return status, out

    def wait(self, session_id: str, states: set[str], timeout: float = 180) -> dict:
        """The session once its state is one of `states`; fails with the last state seen."""
        end, last = time.monotonic() + timeout, None
        while time.monotonic() < end:
            status, last = self.call('GET', f'/sessions/{session_id}')
            if status == 200 and last['state'] in states:
                return last
            time.sleep(2)
        raise AssertionError(f'{session_id} never reached {sorted(states)}; last seen: {last}')

    def wait_for_workers(self, count: int, timeout: float = 60) -> None:
        end, health = time.monotonic() + timeout, None
        while time.monotonic() < end:
            health = self.call('GET', '/health')[1]
            if health['active_workers'] == count:
                return
            time.sleep(2)
        raise AssertionError(f'active_workers never became {count}: {health}')

    def events(self, session_id: str) -> list[dict]:
        """The whole transcript so far, as normalised events (`follow=false`)."""
        status, text = self.raw('GET', f'/sessions/{session_id}/tail?follow=false')
        assert status == 200, text
        return [
            json.loads(line[len('data: ') :])
            for line in text.splitlines()
            if line.startswith('data: ')
        ]


@contextlib.contextmanager
def live_runner(home: Path) -> Iterator[Runner]:
    """Start `python -m marcel_runner.server` on a socket in `home` (cap 1)."""
    if not CWD.is_dir():
        pytest.fail(f'{CWD} is missing: run `make -C deploy install`, or set MARCEL_LIVE_CWD.')
    sock = home / 'runner.sock'
    env = {
        **os.environ,
        'PYTHONPATH': str(SRC),
        'MARCEL_RUNNER_SOCK': str(sock),
        'MARCEL_RUNNER_STATE': str(home / 'state'),
        'MARCEL_WORKSPACES': str(home / 'workspaces'),
        'MARCEL_WORKER_CAP': '1',
        # As marcel-runner.service has it: a session's MCP servers (the channel needs node 22)
        # get the environment of the `claude` the runner starts, not the supervisor's.
        'PATH': f'{NODE_BIN}:{Path.home() / ".local" / "bin"}:/usr/local/bin:/usr/bin:/bin',
    }
    log = (home / 'runner.log').open('w')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'marcel_runner.server'], env=env, stderr=log, stdout=log
    )
    live = Runner(sock)
    try:
        end = time.monotonic() + 30
        while not sock.exists() and proc.poll() is None and time.monotonic() < end:
            time.sleep(0.2)
        if not sock.exists():
            pytest.fail(f'The runner did not start: {(home / "runner.log").read_text()}')
        yield live
    finally:
        for session_id in live.started:
            live.call('POST', f'/sessions/{session_id}/stop')
        proc.terminate()
        proc.wait(timeout=10)
        log.close()
