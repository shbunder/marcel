"""The user-management make targets must not inherit $USER (STORY-260721-02e4f9).

GNU Make imports the environment as make variables, and every login shell sets
``USER``. That made ``if [ -z "$(USER)" ]`` in ``add-user`` / ``remove-user`` /
``link-telegram`` permanently non-empty, so the usage guard never fired and a bare
``make remove-user`` archived the operator's own account.

The Makefile clears it with ``USER :=``. These tests pin both halves of the GNU
Make precedence rule the fix relies on: a simple assignment beats the environment,
and the command line beats the assignment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

TARGETS = ['add-user', 'remove-user', 'link-telegram']

pytestmark = pytest.mark.skipif(shutil.which('make') is None, reason='make is not installed')


def _dry_run(target: str, *args: str) -> str:
    """Expand *target* with `make -n`, with a decoy USER in the environment.

    ``-n`` prints the recipe without running it, so nothing touches the data
    root — the assertions are about what the guard *would* have expanded to.
    """
    env = {**os.environ, 'USER': 'env-decoy-user'}
    proc = subprocess.run(
        ['make', '-n', target, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


@pytest.mark.parametrize('target', TARGETS)
def test_environment_user_does_not_leak_into_target(target: str) -> None:
    """The regression: $USER from the shell must never reach the recipe."""
    assert 'env-decoy-user' not in _dry_run(target)


@pytest.mark.parametrize('target', TARGETS)
def test_guard_sees_an_empty_user(target: str) -> None:
    """With no USER= argument the guard compares against the empty string."""
    assert '[ -z "" ]' in _dry_run(target)


@pytest.mark.parametrize('target', ['add-user', 'remove-user'])
def test_command_line_user_still_wins(target: str) -> None:
    """The documented `make <target> USER=alice` interface is preserved."""
    out = _dry_run(target, 'USER=alice')
    assert '--user "alice"' in out
    assert 'env-decoy-user' not in out


def test_bare_target_exits_non_zero() -> None:
    """A real (non-dry) run with no USER refuses instead of acting."""
    proc = subprocess.run(
        ['make', 'remove-user'],
        cwd=REPO_ROOT,
        env={**os.environ, 'USER': 'env-decoy-user'},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert 'Usage: make remove-user' in proc.stdout
    # The guard fired, so the ops command never ran.
    assert 'marcel_core.ops' not in proc.stdout
