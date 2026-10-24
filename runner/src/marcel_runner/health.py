"""What `GET /health` reports: can the runner start sessions right now?"""

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from marcel_runner import __version__
from marcel_runner.claude_env import scrubbed_env
from marcel_runner.platform import System, current

# The one place the Claude Code version Marcel is tested with lives (S-03.10).
PIN_FILE = Path(__file__).resolve().parents[2] / 'claude-version'


def pinned_version(path: Path = PIN_FILE) -> str:
    """The version in `runner/claude-version`: one line, the bare number (e.g. `2.1.296`)."""
    return path.read_text('utf-8').strip()


@dataclass(frozen=True)
class Settings:
    claude: Path  # absolute: the service's PATH (systemd's, launchd's) has no ~/.local/bin
    cap: int
    timeout: float = 10.0
    expected: str = field(default_factory=pinned_version)


def version_problem(installed: str, expected: str) -> str | None:
    """In plain words, why the installed Claude Code is not the one Marcel is tested with; None
    when it is. `installed` is what `claude --version` printed (`2.1.296 (Claude Code)`); empty
    means claude could not be run, which the health check reports on its own."""
    number = installed.split()[0] if installed.strip() else ''
    if not number or number == expected:
        return None
    return (
        f'Claude Code {number} is installed, but Marcel is tested with {expected}. '
        f'Install {expected}, or test the new version and change runner/claude-version.'
    )


def parse_pid(status: str) -> int | None:
    """The supervisor's pid from `claude daemon status` text, or None. Lenient on purpose."""
    m = re.search(r'\bpid\b[ \t]*[:=]?[ \t]*(\d+)', status, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _run(settings: Settings, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(settings.claude), *args],
        capture_output=True,
        text=True,
        timeout=settings.timeout,
        stdin=subprocess.DEVNULL,
        env=scrubbed_env(os.environ),
    )


def _claude_version(settings: Settings) -> tuple[str, str | None]:
    """(version, reason). The version is empty when claude cannot be run."""
    try:
        p = _run(settings, '--version')
    except FileNotFoundError:
        return '', (
            f'Claude Code is not installed at {settings.claude}. '
            'Install it there, or set MARCEL_CLAUDE to its path.'
        )
    except PermissionError:
        return '', f'The runner cannot run {settings.claude}. Check that the file is executable.'
    except OSError as e:
        return '', f'The runner cannot run {settings.claude}: {e.strerror or e}.'
    except subprocess.TimeoutExpired:
        return '', f'{settings.claude} did not answer `--version` within {settings.timeout:g} s.'
    if p.returncode != 0:
        said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
        return '', f'`{settings.claude} --version` failed: {said}'
    return p.stdout.strip(), None


def _supervisor(settings: Settings, system: System) -> tuple[dict, str | None]:
    """(supervisor object, reason). The reason is None when sessions may start: the supervisor
    would outlive a runner restart, or (on a Mac) none runs, and the next `claude --bg` starts
    one."""
    cmd = f'`{settings.claude} daemon status`'
    down = {'running': False, 'own_unit': False}
    unsure = {'running': True, 'own_unit': False}
    try:
        p = _run(settings, 'daemon', 'status')
    except subprocess.TimeoutExpired:
        return down, f'Claude did not answer {cmd} within {settings.timeout:g} s.'
    except OSError as e:
        return down, f'The runner could not run {cmd}: {e.strerror or e}.'
    if p.returncode == 1:  # documented: exit 1 means the supervisor is not running
        return down, system.no_supervisor()
    if p.returncode != 0:
        said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
        return down, f'{cmd} failed, so the runner cannot tell if a supervisor runs: {said}'
    pid = parse_pid(p.stdout)
    if pid is None:
        return unsure, f'{system.cannot_tell()} {cmd} gave no process id.'
    problem = system.own_service_check(pid)
    if problem is not None:
        return unsure, problem
    return {'running': True, 'own_unit': True}, None


def check(
    settings: Settings,
    active_workers: Callable[[], int],
    system: System | None = None,
    login: Callable[[], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """The `Health` object, and the supervisor's own reason (None when sessions may start as
    far as the supervisor goes). The start-up gate reads the second: a wrong Claude Code version
    does not stop the runner from starting, a supervisor that would die with it does."""
    system = system if system is not None else current()
    claude_version, reason = _claude_version(settings)
    supervisor = {'running': False, 'own_unit': False}
    supervisor_reason = None
    if reason is None:
        supervisor, supervisor_reason = _supervisor(settings, system)
        reason = supervisor_reason
    reasons = [r for r in (version_problem(claude_version, settings.expected), reason) if r]
    reason = ' '.join(reasons) or None
    health: dict[str, Any] = {
        'ok': reason is None,
        'version': __version__,
        'claude_version': claude_version,
        'claude_version_expected': settings.expected,
        'supervisor': supervisor,
        'cap': settings.cap,
        'active_workers': active_workers(),
    }
    if reason is not None:
        health['reason'] = reason
    if login is not None:
        health['login'] = login()
    return health, supervisor_reason


def collect_health(
    settings: Settings,
    active_workers: Callable[[], int],
    system: System | None = None,
    login: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The `Health` object from contracts/runner-api.yaml. Always answers; read `ok`.

    `system` is `platform.Linux()` or `platform.MacOS()` (default: the one this runs on).
    With `login` (a `login.LoginCheck`; only the `/health` route passes it, so a session start
    never waits for `claude auth status`) it adds `login`, which never changes `ok` or `reason`."""
    return check(settings, active_workers, system, login)[0]


# Claude Code changing under the runner (S-03.12) ------------------------------------------------

PROBE_EVERY = 30.0  # seconds between two looks at the version and the supervisor's pid
PROBE_TIMEOUT = 3.0  # seconds each look's command may take: it runs inside the 2-second watch poll
LOG_TAIL = 64 * 1024  # bytes of daemon.log read from its end
SHUTDOWN = re.compile(r'shutting down \(cause=([^,)\s]+)[^)]*?live_workers=(\d+)')
SHUTDOWN_CAUSE = re.compile(r'shutting down \(cause=([^,)\s]+)')


@dataclass(frozen=True)
class Probe:
    """What one look found: `claude --version` ('' when it could not be run) and the supervisor's
    pid (None when none runs or it could not be told)."""

    version: str
    pid: int | None


class ClaudeProbe:
    """Looks at the installed claude and its supervisor at most once per `every` seconds: the
    watcher calls it on each 2-second poll, but only a fresh look runs a process."""

    def __init__(
        self,
        settings: Settings,
        *,
        every: float = PROBE_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Its own short timeout: a stuck claude must not hold the session watch for long.
        self.settings = replace(settings, timeout=min(settings.timeout, PROBE_TIMEOUT))
        self.every, self.clock = every, clock
        self._last: float | None = None

    def __call__(self) -> Probe | None:
        """A fresh look, or None when the last one is younger than `every`."""
        now = self.clock()
        if self._last is not None and now - self._last < self.every:
            return None
        self._last = now
        version, _ = _claude_version(self.settings)
        try:
            p = _run(self.settings, 'daemon', 'status')
            pid = parse_pid(p.stdout) if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            pid = None
        return Probe(version, pid)


def log_size(log: Path) -> int:
    """How far `daemon.log` reaches now (0 when it cannot be read)."""
    try:
        return log.stat().st_size
    except OSError:
        return 0


def last_shutdown(log: Path, since: int = 0) -> dict[str, Any]:
    """`cause` and `live_workers` of the supervisor's last `shutting down (cause=…, …)` line in
    `~/.claude/daemon.log` (project/lessons/runner-built-ins.md) written after byte `since`, so an
    old line never explains a new restart; `{}` when there is none or it cannot be read. A log
    shorter than `since` was rotated: its tail is read."""
    try:
        with log.open('rb') as f:
            end = f.seek(0, os.SEEK_END)
            start = since if 0 < since <= end else max(0, end - LOG_TAIL)
            f.seek(max(start, end - LOG_TAIL))
            text = f.read().decode('utf-8', errors='replace')
    except OSError:
        return {}
    found = [m for m in SHUTDOWN_CAUSE.finditer(text)]
    if not found:
        return {}
    last = found[-1]
    out: dict[str, Any] = {'cause': last.group(1)}
    full = SHUTDOWN.match(text, last.start())
    if full is not None:
        out['live_workers'] = int(full.group(2))
    return out
