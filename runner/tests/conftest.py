"""Shared helpers: a fake `claude` script, and a tiny HTTP client for a unix socket."""

import http.client
import json
import os
import socket
import stat
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from marcel_runner.health import pinned_version
from marcel_runner.platform import Linux, MacOS

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


def _apart(pid: int) -> str:
    """cgroup stub: the supervisor lives in another unit than this process."""
    return 'runner.service' if pid == os.getpid() else 'marcel-claude-daemon.service'


# The Linux checks, with the supervisor in its own unit: what most tests run against, on any system.
apart = Linux(_apart)


# `claude daemon status` on a Mac with the on-demand supervisor up, recorded on the owner's MacBook
# with Claude Code 2.1.310 (exit code 0; S-22.3's drill, 2026-10-24): a supervisor started by the
# first `claude --bg`. The pid is 1, the home path is changed.
DAEMON_STATUS_RUNNING_MAC = """\
pid:     1
version: 2.1.310
uptime:  8s
origin:  transient — started on-demand by `claude --bg` (pid 57553) in /Users/m
config:  /Users/m/.claude/daemon.json
log:     /Users/m/.claude/daemon.log

bg sessions:
  sock dir:     /tmp/cc-daemon-501/33cacad1
  control.sock: reachable
  bg workers:   1 running (control.sock), 1 in roster.json
  roster.json:  updated 6s ago
  daemon.log:   7.9KB at /Users/m/.claude/daemon.log

holding this daemon open:
  1 bg worker running (daemon waits for them to settle)

to let it idle-exit: wait for (or cancel) bg workers and close any `claude agents`
"""

RUNNER_PGID = 4242  # the runner job's process group in the fake `ps`

FAKE_PS = """#!{python}
import json, sys
args = sys.argv[1:]
with open({calls!r}, 'a') as f:
    f.write(json.dumps(args) + '\\n')
table = {table!r}
wanted = args[args.index('-p') + 1].split(',')
for pid in wanted:
    if pid in table:
        print(table[pid])
"""


def fake_ps(tmp_path: Path, supervisor_pgid: int, *, runner: bool = True) -> Path:
    """A `ps` that knows two processes: this one (the runner, group RUNNER_PGID) and the supervisor
    (pid 1, ppid 1 as SP11 saw it, group `supervisor_pgid`). Like macOS `ps -o pid=,ppid=,pgid= -p
    a,b` it prints the rows of the pids it knows and leaves out the others, exit 0. Each call's
    arguments go to `ps.calls`, one JSON list per line."""
    table = {'1': f'    1     1 {supervisor_pgid:5d}'}
    if runner:
        table[str(os.getpid())] = f'{os.getpid():5d}     1 {RUNNER_PGID:5d}'
    ps = tmp_path / 'ps'
    ps.write_text(
        FAKE_PS.format(python=sys.executable, calls=str(tmp_path / 'ps.calls'), table=table)
    )
    ps.chmod(0o755)
    return ps


def mac(tmp_path: Path, supervisor_pgid: int = 1, **kw) -> MacOS:
    """The macOS checks with a fake `ps`. The default: the supervisor leads its own group."""
    return MacOS(ps=fake_ps(tmp_path, supervisor_pgid, **kw))


def mac_claude(tmp_path: Path, running: bool = True) -> Path:
    """A claude as on a Mac: the on-demand supervisor is up (DAEMON_STATUS_RUNNING_MAC), or none
    runs (exit 1, DAEMON_STATUS_NOT_RUNNING)."""
    status = tmp_path / 'daemon-status.txt'
    status.write_text(DAEMON_STATUS_RUNNING_MAC if running else DAEMON_STATUS_NOT_RUNNING)
    return write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n'
        f'if [ "$1" = "daemon" ]; then cat "{status}"; exit {0 if running else 1}; fi\n'
        'exit 2\n',
    )
