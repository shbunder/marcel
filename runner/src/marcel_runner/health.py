"""What `GET /health` reports: can the runner start sessions right now?"""

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marcel_runner import __version__

SUPERVISOR_UNIT = 'marcel-claude-daemon.service'


@dataclass(frozen=True)
class Settings:
    claude: Path  # absolute: systemd's user PATH has no ~/.local/bin
    cap: int
    timeout: float = 10.0


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
    health: dict[str, Any] = {
        'ok': reason is None,
        'version': __version__,
        'claude_version': claude_version,
        'supervisor': supervisor,
        'cap': settings.cap,
        'active_workers': active_workers(),
    }
    if reason is not None:
        health['reason'] = reason
    return health
