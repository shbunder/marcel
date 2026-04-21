"""Tests for scripts/redeploy.sh — env-scoped flag-file cleanup.

The dev container has no in-container watchdog (uvicorn is PID 1), so
``redeploy.sh`` itself must clear the ``restart_requested.{env}`` flag.
Leaving it in place re-triggers ``marcel-dev-redeploy.path`` on any
subsequent systemd restart / host reboot. See ISSUE-5ca6dc.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_REDEPLOY_SH = _REPO_ROOT / 'scripts' / 'redeploy.sh'


def _seed_flag(home: pathlib.Path, env: str, contents: str = 'deadbeef') -> pathlib.Path:
    flag_dir = home / '.marcel' / 'watchdog'
    flag_dir.mkdir(parents=True, exist_ok=True)
    path = flag_dir / f'restart_requested.{env}'
    path.write_text(contents)
    return path


def _run_dry(env: str, home: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Invoke ``redeploy.sh --env <env> --force`` with ``DRY_RUN=1``.

    ``DRY_RUN=1`` short-circuits the script after the flag-file cleanup,
    so the test never needs a working docker runtime.
    """
    proc_env = os.environ.copy()
    proc_env['HOME'] = str(home)
    proc_env['DRY_RUN'] = '1'
    proc_env.pop('MARCEL_DATA_DIR', None)
    return subprocess.run(
        ['bash', str(_REDEPLOY_SH), '--env', env, '--force'],
        env=proc_env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize('env', ['dev', 'prod'])
def test_redeploy_clears_env_flag(env: str, tmp_path: pathlib.Path) -> None:
    flag = _seed_flag(tmp_path, env)
    assert flag.exists()

    result = _run_dry(env, tmp_path)

    assert result.returncode == 0, f'redeploy.sh exited {result.returncode}: {result.stderr}'
    assert not flag.exists(), f'flag still present after redeploy: {flag}'


def test_redeploy_does_not_touch_other_env_flag(tmp_path: pathlib.Path) -> None:
    """Running ``--env dev`` must leave ``restart_requested.prod`` alone."""
    dev_flag = _seed_flag(tmp_path, 'dev')
    prod_flag = _seed_flag(tmp_path, 'prod', contents='unrelated')

    _run_dry('dev', tmp_path)

    assert not dev_flag.exists()
    assert prod_flag.exists(), 'the other-env flag must not be cleared'
    assert prod_flag.read_text() == 'unrelated'


def test_redeploy_no_flag_is_idempotent(tmp_path: pathlib.Path) -> None:
    """``rm -f`` on an absent flag is a no-op, not an error."""
    (tmp_path / '.marcel' / 'watchdog').mkdir(parents=True)
    result = _run_dry('dev', tmp_path)
    assert result.returncode == 0
