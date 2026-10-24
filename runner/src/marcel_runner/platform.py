"""What differs for the runner between Linux (the NUC) and macOS (a Mac laptop), S-22.3.

The one question both answer is `own_service_check(pid)`: would Claude's supervisor (process
`pid`) outlive a restart of the runner's own service? If it would not, a runner restart ends
every session, so the runner refuses to start sessions.

- **Linux**: the supervisor runs in its own systemd unit, `marcel-claude-daemon.service`. The
  runner compares `/proc/<pid>/cgroup` with its own.
- **macOS**: Claude Code starts its supervisor on demand, from the first `claude --bg`, and it
  is shared with the user's own sessions (SP11). `launchctl kickstart -k` of the runner's
  LaunchAgent kills the runner job's process group. SP11 (2026-10-24) measured that the on-demand
  supervisor leads its own group (ppid 1) and survives that restart. So the check is: the
  supervisor's process group (`ps -o pgid`) is not the runner's. `ps -o sess` prints 0 for every
  process on macOS, so the session is not read. No supervisor at all is fine on a Mac: the next
  `claude --bg` starts one.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

SUPERVISOR_UNIT = 'marcel-claude-daemon.service'

MAC_REFUSAL = (
    "Claude's background supervisor would stop when Marcel's runner restarts, which would end "
    'every session. Restart the runner with `marcel start runner`.'
)


def cgroup_of(pid: int) -> str:
    return Path(f'/proc/{pid}/cgroup').read_text()


def marcel_home(env: Mapping[str, str]) -> Path:
    """`MARCEL_HOME`, or `~/.marcel` (S-22.1, as the hub reads it)."""
    home = env.get('MARCEL_HOME')
    return Path(home).expanduser() if home else Path.home() / '.marcel'


class Linux:
    """The NUC: the supervisor is a systemd user unit of its own."""

    name = 'linux'
    service_manager = "systemd's user PATH"
    supervisor_hint = f"check Claude's supervisor ({SUPERVISOR_UNIT})"

    def __init__(self, read_cgroup: Callable[[int], str] = cgroup_of) -> None:
        self.read_cgroup = read_cgroup

    def no_supervisor(self) -> str | None:
        return (
            f'Claude has no background supervisor running. '
            f'Start {SUPERVISOR_UNIT} (`systemctl --user start {SUPERVISOR_UNIT}`).'
        )

    def cannot_tell(self) -> str:
        return (
            f"The runner could not tell where Claude's supervisor runs, so it will not start "
            f'sessions. Check that it runs in {SUPERVISOR_UNIT}.'
        )

    def own_service_check(self, pid: int) -> str | None:
        """None when the supervisor runs in another cgroup than the runner; else why not."""
        try:
            own = self.read_cgroup(os.getpid())
        except OSError:
            return f'{self.cannot_tell()} The runner could not read its own cgroup.'
        try:
            theirs = self.read_cgroup(pid)
        except OSError:
            return f'{self.cannot_tell()} The runner could not read the cgroup of process {pid}.'
        if theirs == own:
            return (
                f"Claude's supervisor runs inside the runner, so a runner restart would kill "
                f'every session. Stop it, start {SUPERVISOR_UNIT}, then restart the runner.'
            )
        return None

    def default_socket(self, env: Mapping[str, str]) -> Path:
        runtime = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
        return Path(runtime) / 'marcel' / 'runner.sock'


@dataclass(frozen=True)
class Proc:
    """One `ps` row."""

    pid: int
    ppid: int
    pgid: int


def parse_ps(text: str) -> dict[int, Proc]:
    """`ps -o pid=,ppid=,pgid=` rows by pid; lines that are not three numbers are skipped."""
    out: dict[int, Proc] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            pid, ppid, pgid = map(int, parts)
            out[pid] = Proc(pid, ppid, pgid)
    return out


class MacOS:
    """A Mac: Claude's supervisor starts on demand and is shared with the user's sessions."""

    name = 'macos'
    service_manager = "launchd's PATH"
    supervisor_hint = 'check that `claude daemon status` answers'

    def __init__(
        self,
        ps: Path = Path('/bin/ps'),
        own_pid: Callable[[], int] = os.getpid,
        timeout: float = 5.0,
    ) -> None:
        self.ps, self.own_pid, self.timeout = ps, own_pid, timeout

    def no_supervisor(self) -> str | None:
        return None

    def cannot_tell(self) -> str:
        return (
            "The runner could not tell whether Claude's background supervisor would outlive a "
            'runner restart, so it will not start sessions.'
        )

    def own_service_check(self, pid: int) -> str | None:
        """None when the supervisor is in another process group than the runner; else why not."""
        own = self.own_pid()
        try:
            p = subprocess.run(
                [str(self.ps), '-o', 'pid=,ppid=,pgid=', '-p', f'{own},{pid}'],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return f'{self.cannot_tell()} `ps` did not answer within {self.timeout:g} s.'
        except OSError as e:
            return f'{self.cannot_tell()} The runner could not run {self.ps}: {e.strerror or e}.'
        # ps exits 1 when one of the pids is gone, and still prints the other: read the rows.
        rows = parse_ps(p.stdout)
        if own not in rows:
            return f'{self.cannot_tell()} `ps` did not show the runner itself (process {own}).'
        if pid not in rows:
            return f'{self.cannot_tell()} `ps` did not show the supervisor (process {pid}).'
        if rows[pid].pgid == rows[own].pgid:
            return MAC_REFUSAL
        return None

    def default_socket(self, env: Mapping[str, str]) -> Path:
        return marcel_home(env) / 'run' / 'runner.sock'


System = Linux | MacOS


def current() -> System:
    """The system the runner runs on. Anything that is not macOS gets the Linux checks."""
    return MacOS() if sys.platform == 'darwin' else Linux()
