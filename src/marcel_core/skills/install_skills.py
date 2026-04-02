"""Install Marcel integration skills into .claude/skills/ for the Claude Agent SDK.

This script creates symlinks (dev) or copies (prod/Docker) from the source
skill docs in ``src/marcel_core/skills/docs/`` to ``.claude/skills/`` where
the Claude Code tools preset auto-discovers them.

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
_TARGET = _REPO_ROOT / '.claude' / 'skills'

# Skills that live in .claude/skills/ as part of the repo (not managed by this script)
_REPO_SKILLS = {'new-issue', 'finish-issue'}


def install(*, copy: bool = False) -> None:
    """Install integration skill docs into .claude/skills/.

    Args:
        copy: If True, copy files instead of symlinking (for Docker builds
              where the source path may not exist at runtime).
    """
    _TARGET.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(_SKILLS_DOCS.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith('_'):
            continue
        if skill_dir.name in _REPO_SKILLS:
            continue

        target = _TARGET / skill_dir.name

        # Clean up existing symlink or directory
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

        if copy:
            shutil.copytree(skill_dir, target)
        else:
            target.symlink_to(skill_dir.resolve())

        mode = 'copied' if copy else 'linked'
        print(f'  {mode}: {skill_dir.name}/ -> .claude/skills/{skill_dir.name}/')


if __name__ == '__main__':
    copy = '--copy' in sys.argv
    print(f'Installing Marcel integration skills ({"copy" if copy else "symlink"} mode)...')
    install(copy=copy)
    print('Done.')
