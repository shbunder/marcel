"""What `GET /health` reports: can the runner start sessions right now?"""

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marcel_runner import __version__
from marcel_runner.claude_env import scrubbed_env

SUPERVISOR_UNIT = 'marcel-claude-daemon.service'


# The one place the Claude Code version Marcel is tested with lives (S-03.10).
PIN_FILE = Path(__file__).resolve().parents[2] / 'claude-version'


def pinned_version(path: Path = PIN_FILE) -> str:
    """The version in `runner/claude-version`: one line, the bare number (e.g. `2.1.296`)."""
    return path.read_text('utf-8').strip()


@dataclass(frozen=True)
class Settings:
    claude: Path  # absolute: systemd's user PATH has no ~/.local/bin
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


def cgroup_of(pid: int) -> str:
    return Path(f'/proc/{pid}/cgroup').read_text()


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


def _supervisor(
    settings: Settings, own_cgroup: str | None, read_cgroup: Callable[[int], str]
) -> tuple[dict, str | None]:
    """(supervisor object, reason). The reason is None only when it runs in its own unit."""
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
        return down, (
            f'Claude has no background supervisor running. '
            f'Start {SUPERVISOR_UNIT} (`systemctl --user start {SUPERVISOR_UNIT}`).'
        )
    if p.returncode != 0:
        said = (p.stderr or p.stdout).strip() or f'exit code {p.returncode}'
        return down, f'{cmd} failed, so the runner cannot tell if a supervisor runs: {said}'
    unknown = (
        f"The runner could not tell where Claude's supervisor runs, so it will not start "
        f'sessions. Check that it runs in {SUPERVISOR_UNIT}.'
    )
    pid = parse_pid(p.stdout)
    if pid is None:
        return unsure, f'{unknown} {cmd} gave no process id.'
    if own_cgroup is None:
        return unsure, f'{unknown} The runner could not read its own cgroup.'
    try:
        theirs = read_cgroup(pid)
    except OSError:
        return unsure, f'{unknown} The runner could not read the cgroup of process {pid}.'
    if theirs == own_cgroup:
        return unsure, (
            f"Claude's supervisor runs inside the runner, so a runner restart would kill "
            f'every session. Stop it, start {SUPERVISOR_UNIT}, then restart the runner.'
        )
    return {'running': True, 'own_unit': True}, None


def collect_health(
    settings: Settings,
    active_workers: Callable[[], int],
    cgroup_of: Callable[[int], str] = cgroup_of,
) -> dict[str, Any]:
    """The `Health` object from contracts/runner-api.yaml. Always answers; read `ok`."""
    claude_version, reason = _claude_version(settings)
    supervisor = {'running': False, 'own_unit': False}
    if reason is None:
        try:
            own: str | None = cgroup_of(os.getpid())
        except OSError:
            own = None
        supervisor, reason = _supervisor(settings, own, cgroup_of)
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
    return health


# Claude Code changing under the runner (S-03.12) ------------------------------------------------

PROBE_EVERY = 30.0  # seconds between two looks at the version and the supervisor's pid
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
        self.settings, self.every, self.clock = settings, every, clock
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


def last_shutdown(log: Path) -> dict[str, Any]:
    """`cause` and `live_workers` of the supervisor's last `shutting down (cause=…, …)` line in
    `~/.claude/daemon.log` (project/lessons/runner-built-ins.md); `{}` when it cannot be read."""
    try:
        with log.open('rb') as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - LOG_TAIL))
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
