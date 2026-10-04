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
    claude: Path  # absolute: systemd's user PATH has no ~/.local/bin (SP6)
    cap: int
    timeout: float = 10.0


def parse_supervisor(status: str) -> tuple[bool, int | None]:
    """Read `claude daemon status`: (running, pid). The pid is None when the text has none."""
    lines = status.strip().splitlines()
    if not lines or not lines[0].lower().startswith('running'):
        return False, None
    m = re.search(r'\bpid\b[^\d\n]{0,20}(\d+)', status)
    return True, int(m.group(1)) if m else None


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


def _supervisor(settings: Settings, own_cgroup: str, read_cgroup: Callable[[int], str]) -> dict:
    try:
        running, pid = parse_supervisor(_run(settings, 'daemon', 'status').stdout)
    except (OSError, subprocess.SubprocessError):
        return {'running': False, 'own_unit': False}
    if not running:
        return {'running': False, 'own_unit': False}
    if pid is None:
        return {'running': True, 'own_unit': False}
    try:
        own_unit = read_cgroup(pid) != own_cgroup
    except OSError:
        own_unit = False
    return {'running': True, 'own_unit': own_unit}


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
            own = cgroup_of(os.getpid())
        except OSError:
            own = ''
        supervisor = _supervisor(settings, own, cgroup_of)
        if not supervisor['running']:
            reason = (
                f'Claude has no background supervisor running. '
                f'Start {SUPERVISOR_UNIT} (`systemctl --user start {SUPERVISOR_UNIT}`).'
            )
        elif not supervisor['own_unit']:
            reason = (
                f"Claude's supervisor runs inside the runner, so a runner restart would kill "
                f'every session. Stop it, start {SUPERVISOR_UNIT}, then restart the runner.'
            )
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
