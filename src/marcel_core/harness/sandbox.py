"""Bubblewrap workspace-write sandbox for agent command/code execution.

Confines ``bash`` (and, in F3, ``code_exec``) under bubblewrap with a
**workspace-write** policy (ADR-260628-0fc1e2):

- broad **read-only** view of the whole filesystem,
- **writes confined** to the session workspace (the effective cwd),
- the **self-modification boundary** — ``.git``, the data dir (``~/.marcel``,
  which holds the ``restart_requested.{env}`` flag), and the tracked
  self-mod files (``CLAUDE.md`` / ``auth`` / ``config.py`` / ``.env*``) —
  bind-mounted **read-only inside** the writable workspace, so sandboxed
  code cannot rewrite the code that governs it or inject a restart,
- network optionally isolated.

**Requires unprivileged user namespaces.** Several environments disable
them: Ubuntu 23.10+ restrict unprivileged userns via AppArmor even when
``kernel.unprivileged_userns_clone`` is on, and Docker blocks them without
the right ``--security-opt``. :func:`sandbox_available` probes at runtime;
when the sandbox cannot start, Marcel runs the command **unsandboxed** with
a warning (the command policy still gates dangerous commands). The OS
sandbox is therefore *active only where userns is available* — enable it in
the deployment (see docs) to turn it on. This is the containment boundary
the command policy is not; until it is active, treat bash as unconfined.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def bwrap_path() -> str | None:
    """Return the ``bwrap`` executable path, or ``None`` if not installed."""
    return shutil.which('bwrap')


@functools.cache
def sandbox_available() -> bool:
    """True iff bubblewrap is installed *and* a smoke sandbox actually starts.

    Runs a throwaway ``bwrap … true`` to confirm unprivileged user namespaces
    work here — installed-but-can't-start (AppArmor / Docker restriction) is
    the common failure and must resolve to ``False`` so the caller falls
    back rather than silently no-op'ing the tool. Cached — call
    ``sandbox_available.cache_clear()`` in tests.
    """
    executable = bwrap_path()
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, '--unshare-user', '--unshare-pid', '--ro-bind', '/', '/', '--die-with-parent', 'true'],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        log.warning(
            'bubblewrap is installed but the sandbox could not start (unprivileged user namespaces unavailable?): %s',
            result.stderr.decode('utf-8', errors='replace').strip()[:200],
        )
        return False
    return True


def _readonly_self_mod_binds(workspace: Path, data_dir: Path) -> list[str]:
    """``--ro-bind`` the self-mod boundary read-only, *inside* the writable workspace."""
    binds: list[str] = []

    def ro(path: Path) -> None:
        if path.exists():
            binds.extend(['--ro-bind', str(path), str(path)])

    ro(workspace / '.git')  # hook/ref injection vector
    ro(data_dir)  # ~/.marcel — user data + the restart flag
    ro(workspace / 'CLAUDE.md')
    ro(workspace / 'src' / 'marcel_core' / 'auth')
    ro(workspace / 'src' / 'marcel_core' / 'config.py')
    for env_file in sorted(workspace.glob('.env*')):
        ro(env_file)
    return binds


def build_bwrap_argv(
    *,
    workspace: Path,
    cwd: Path,
    data_dir: Path,
    allow_network: bool,
) -> list[str]:
    """Build the bubblewrap argv (without the trailing command) for one run."""
    executable = bwrap_path()
    if executable is None:  # pragma: no cover - guarded by sandbox_available()
        raise RuntimeError('bwrap not available')

    argv = [
        executable,
        '--unshare-user',
        '--unshare-pid',
        '--unshare-ipc',
        '--unshare-uts',
        '--unshare-cgroup',
        '--die-with-parent',
        '--new-session',
        '--ro-bind',
        '/',
        '/',  # broad read-only root
        '--dev',
        '/dev',
        '--proc',
        '/proc',
        '--tmpfs',
        '/tmp',
        '--bind',
        str(workspace),
        str(workspace),  # the one writable tree
    ]
    argv += _readonly_self_mod_binds(workspace, data_dir)
    if not allow_network:
        argv += ['--unshare-net']
    argv += ['--chdir', str(cwd)]
    return argv


async def run_sandboxed(
    command: str,
    *,
    workspace: Path,
    cwd: Path,
    data_dir: Path,
    timeout: float,
    allow_network: bool = True,
) -> tuple[int | None, bytes, bytes]:
    """Run ``bash -c command`` inside the sandbox; return ``(returncode, stdout, stderr)``.

    Raises :class:`asyncio.TimeoutError` if the command exceeds ``timeout``
    (the process is killed first). ``allow_network`` defaults to True so the
    admin ``bash`` surface keeps working; the untrusted ``code_exec`` path
    (F3) runs with it False.
    """
    argv = build_bwrap_argv(
        workspace=workspace,
        cwd=cwd,
        data_dir=data_dir,
        allow_network=allow_network,
    )
    argv += ['bash', '-c', command]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode, stdout, stderr
