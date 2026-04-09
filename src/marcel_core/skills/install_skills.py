"""Install Marcel integration skills into .marcel/skills/ for runtime discovery.

This script creates symlinks (dev) or copies (prod/Docker) from the source
skill docs in ``src/marcel_core/skills/docs/`` to ``.marcel/skills/`` where
the skill loader auto-discovers them.

Usage:
    python -m marcel_core.skills.install_skills          # symlink (dev)
    python -m marcel_core.skills.install_skills --copy   # copy (Docker/prod)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_SKILLS_DOCS = Path(__file__).parent / 'docs'
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = _REPO_ROOT / '.marcel' / 'skills'


def install(*, copy: bool = False) -> None:
    """Install integration skill docs into .marcel/skills/.

    Only installs skills that have a source directory in ``src/marcel_core/skills/docs/``.
    Skills already in ``.marcel/skills/`` (e.g. manually created ones) are left untouched.

    Args:
        copy: If True, copy files instead of symlinking (for Docker builds
              where the source path may not exist at runtime).
    """
    _TARGET.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(_SKILLS_DOCS.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith('_'):
            continue

        target = _TARGET / skill_dir.name

        # Skip if the target already exists and is a real directory (not a symlink).
        # This means it was manually placed in .marcel/skills/ and should not be
        # overwritten by the install script.
        if target.is_dir() and not target.is_symlink():
            print(f'  skipped: {skill_dir.name}/ (already exists in .marcel/skills/)')
            continue

        # Clean up existing symlink
        if target.is_symlink():
            target.unlink()

        if copy:
            shutil.copytree(skill_dir, target)
        else:
            target.symlink_to(skill_dir.resolve())

        mode = 'copied' if copy else 'linked'
        print(f'  {mode}: {skill_dir.name}/ -> .marcel/skills/{skill_dir.name}/')


if __name__ == '__main__':
    copy = '--copy' in sys.argv
    print(f'Installing Marcel integration skills ({"copy" if copy else "symlink"} mode)...')
    install(copy=copy)
    print('Done.')
