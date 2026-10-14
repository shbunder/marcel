"""Shared helpers: a fake `claude` script, and a tiny HTTP client for a unix socket."""

import http.client
import json
import socket
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from marcel_runner.health import pinned_version

# `claude daemon status` with no supervisor, recorded on the NUC with Claude Code 2.1.289 (exit
# code 1). Only the home path is changed.
DAEMON_STATUS_NOT_RUNNING = """\
not running

bg sessions:
  sock dir:     /tmp/cc-daemon-1000/6298d98a
  control.sock: unreachable (connect ENOENT /tmp/cc-daemon-1000/6298d98a/control.sock)
  bg workers:   0 in roster.json (control unreachable)
  roster.json:  updated 34144s ago
  daemon.log:   9.4KB at /home/marcel/.claude/daemon.log
"""


# `claude daemon status` with the supervisor up in marcel-claude-daemon.service, recorded on the
# NUC with Claude Code 2.1.296 (exit code 0; S-03.8). The home path is changed and the pid is 1.
DAEMON_STATUS_RUNNING = """\
pid:     1
version: 2.1.296
uptime:  2755s
origin:  foreground
config:  /home/marcel/.claude/daemon.json
log:     /home/marcel/.claude/daemon.log

bg sessions:
  sock dir:     /tmp/cc-daemon-1000/6298d98a
  control.sock: reachable
  bg workers:   1 running (control.sock), 1 in roster.json
  roster.json:  updated 486s ago
  daemon.log:   14.9KB at /home/marcel/.claude/daemon.log
"""


# What `claude --version` prints for the pinned version (runner/claude-version, S-03.10).
CLAUDE_VERSION = pinned_version()
CLAUDE_VERSION_LINE = f'{CLAUDE_VERSION} (Claude Code)'


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__('runner')
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(self._path)


def request(sock: Path, method: str, path: str) -> tuple[int, dict]:
    conn = UnixConnection(str(sock))
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def own_state_folder(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own runner state folder. The gate runs on the NUC, next to the live
    runner: a test that runs `server.main()` once wrote its `login.json` into the live one."""
    monkeypatch.setenv('MARCEL_RUNNER_STATE', str(tmp_path_factory.mktemp('runner-state')))


RUNNER_API = Path(__file__).resolve().parents[2] / 'contracts' / 'runner-api.yaml'


def runner_api(schema: str) -> Draft202012Validator:
    """A validator for one schema in contracts/runner-api.yaml (OpenAPI 3.1: JSON Schema 2020-12),
    its `$ref`s resolved within the file."""
    import yaml

    spec = yaml.safe_load(RUNNER_API.read_text('utf-8'))
    return Draft202012Validator(
        {'$ref': f'#/components/schemas/{schema}', 'components': spec['components']}
    )


def write_script(path: Path, body: str) -> Path:
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# `claude auth status --json`, recorded on the NUC with Claude Code 2.1.296 (S-03.13). Logged out it
# exits 1 and still prints the JSON. The email, org id and org name are placeholders.
LOGIN_FIXTURES = Path(__file__).parent / 'fixtures' / 'login'
AUTH_LOGGED_IN = (LOGIN_FIXTURES / 'auth-status-logged-in.json').read_text('utf-8')
AUTH_LOGGED_OUT = (LOGIN_FIXTURES / 'auth-status-logged-out.json').read_text('utf-8')


def auth_answer(claude: Path, text: str, code: int = 0) -> None:
    """What the fake claude next to `claude` prints for `auth status --json`, and its exit code."""
    (claude.parent / 'auth-status.txt').write_text(text)
    (claude.parent / 'auth-status.code').write_text(f'{code}\n')


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """A claude whose supervisor runs: `daemon status` exits 0 and names pid 1 (not our cgroup),
    and which is logged in: `auth status --json` prints AUTH_LOGGED_IN (change it with
    `auth_answer`).

    Both status texts are recorded on the NUC: DAEMON_STATUS_RUNNING and
    DAEMON_STATUS_NOT_RUNNING.
    """
    status = tmp_path / 'daemon-status.txt'
    status.write_text(DAEMON_STATUS_RUNNING)
    claude = write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n'
        f'if [ "$1" = "daemon" ]; then cat "{status}"; exit 0; fi\n'
        'if [ "$1 $2 $3" = "auth status --json" ]; then\n'
        f'  echo x >> "{tmp_path / "auth-status.calls"}"\n'
        f'  cat "{tmp_path / "auth-status.txt"}"; exit "$(cat "{tmp_path / "auth-status.code"}")"\n'
        'fi\n'
        'exit 2\n',
    )
    auth_answer(claude, AUTH_LOGGED_IN)
    return claude


def apart(pid: int) -> str:
    """cgroup stub: the supervisor lives in another unit than this process."""
    import os

    return 'runner.service' if pid == os.getpid() else 'marcel-claude-daemon.service'
