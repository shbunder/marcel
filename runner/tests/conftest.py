"""Shared helpers: a fake `claude` script, and a tiny HTTP client for a unix socket."""

import http.client
import json
import socket
import stat
from pathlib import Path

import pytest

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

    The exit codes are documented (`daemon status` exits 1 when the supervisor is not running).
    The status TEXT of the running case is a guess, not recorded output. The not-running text is
    recorded: DAEMON_STATUS_NOT_RUNNING.
    """
    return write_script(
        tmp_path / 'claude',
        'if [ "$1" = "--version" ]; then echo "2.1.289 (Claude Code)"; exit 0; fi\n'
        'if [ "$1" = "daemon" ]; then\n'
        '  echo "running"; echo "  pid:          1"; echo "  origin:       foreground"; exit 0\n'
        'fi\n'
        'exit 2\n',
    )


def apart(pid: int) -> str:
    """cgroup stub: the supervisor lives in another unit than this process."""
    import os

    return 'runner.service' if pid == os.getpid() else 'marcel-claude-daemon.service'
