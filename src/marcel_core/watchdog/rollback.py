"""Git rollback helper for the Marcel watchdog.

Reverts the HEAD commit and creates a new revert commit so the repo stays on a
clean, linear history.  Only the standard library and ``git`` (which must be on
PATH) are required.
"""

from __future__ import annotations

import pathlib
import subprocess


def do_rollback(repo_root: pathlib.Path) -> None:
    """Revert HEAD and create a new revert commit.

    Runs ``git revert HEAD --no-edit`` in *repo_root*.  The ``--no-edit`` flag
    causes git to use the auto-generated revert commit message without opening
    an editor, and the revert command itself creates the commit — no separate
    ``git commit`` step is needed.

    Args:
        repo_root: Absolute path to the root of the git repository.

    Raises:
        subprocess.CalledProcessError: If the git command exits with a non-zero
            status (e.g. merge conflict during revert).
    """
    subprocess.run(
        ['git', 'revert', 'HEAD', '--no-edit'],
        cwd=repo_root,
        check=True,
    )
