"""Shared helpers: a fake `claude` script, and a tiny HTTP client for a unix socket."""

import http.client
import json
import socket
import stat
from pathlib import Path

import pytest

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


def write_script(path: Path, body: str) -> Path:
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    """A claude whose supervisor runs: `daemon status` exits 0 and names pid 1 (not our cgroup).

    Both status texts are recorded on the NUC: DAEMON_STATUS_RUNNING and
    DAEMON_STATUS_NOT_RUNNING.
    """
    status = tmp_path / 'daemon-status.txt'
    status.write_text(DAEMON_STATUS_RUNNING)
    return write_script(
        tmp_path / 'claude',
        f'if [ "$1" = "--version" ]; then echo "{CLAUDE_VERSION_LINE}"; exit 0; fi\n'
        f'if [ "$1" = "daemon" ]; then cat "{status}"; exit 0; fi\n'
        'exit 2\n',
    )


def apart(pid: int) -> str:
    """cgroup stub: the supervisor lives in another unit than this process."""
    import os

    return 'runner.service' if pid == os.getpid() else 'marcel-claude-daemon.service'
