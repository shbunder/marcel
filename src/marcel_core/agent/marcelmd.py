"""MARCEL.md loader — discover and load personal assistant instruction files.

Inspired by Claude Code's CLAUDE.md loading system.  MARCEL.md files provide
instructions to Marcel when acting as a personal assistant, separate from the
developer-focused CLAUDE.md files that govern code changes.

## Loading order (lowest to highest priority)

1. ``~/.marcel/MARCEL.md`` — user home base (via MARCEL_DATA_DIR if set)
2. ``MARCEL.md`` / ``.marcel/MARCEL.md`` — project files, walked from filesystem
   root down to CWD (files closer to CWD have higher priority, i.e. are loaded
   later and can extend or override earlier files)

All discovered files are concatenated; higher-priority files come last so their
instructions take precedence in models that weight later context more.

## Use cases

- ``~/.marcel/MARCEL.md`` — who the user is, household members, global preferences
- ``.marcel/MARCEL.md`` at project root — Marcel's identity, tone, skills overview
- ``MARCEL.md`` in a sub-project — context-specific assistant rules

## Separation from CLAUDE.md

CLAUDE.md files are read by the inner Claude Code agent when Marcel performs
code changes or self-modification.  MARCEL.md files are read by Marcel in
personal assistant mode.  This keeps the two concerns clearly separated.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Filenames to look for at each directory level
_FILENAMES = ('MARCEL.md', '.marcel/MARCEL.md')


def _home_marcelmd() -> Path:
    """Return the home-level MARCEL.md path.

    Uses ``MARCEL_DATA_DIR`` when set (matches Docker/production setup),
    otherwise falls back to ``~/.marcel/MARCEL.md``.
    """
    env = os.environ.get('MARCEL_DATA_DIR')
    if env:
        return Path(env) / 'MARCEL.md'
    return Path.home() / '.marcel' / 'MARCEL.md'


def _project_root() -> Path:
    """Return the Marcel project root (where this module lives)."""
    # src/marcel_core/agent/marcelmd.py → parents[3] = project root
    return Path(__file__).resolve().parents[3]


def _dirs_from_root_to_cwd() -> list[Path]:
    """Return directories from filesystem root down to CWD.

    Like Claude Code, we walk from root to CWD so that files closer to CWD
    are loaded last and therefore have higher priority.
    """
    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    project_root = _project_root().resolve()

    # Collect unique directories: project root + cwd ancestry up to home
    dirs: list[Path] = []
    # Walk from CWD upward, stop at home boundary
    p = cwd
    while True:
        dirs.append(p)
        if p == home or p == p.parent:
            break
        p = p.parent

    # Also include the project root if it's not already in the walk
    if project_root not in dirs:
        dirs.append(project_root)

    # Reverse so root is first (loaded first = lowest priority)
    dirs.reverse()
    return dirs


def load_marcelmd_files() -> list[tuple[str, str]]:
    """Discover and load all MARCEL.md files.

    Returns a list of ``(label, content)`` pairs in loading order
    (home first, CWD-closest last).  Empty files are skipped.
    """
    results: list[tuple[str, str]] = []
    seen: set[Path] = set()

    def _add(path: Path, label: str) -> None:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            return
        seen.add(resolved)
        if not path.exists():
            return
        content = path.read_text(encoding='utf-8').strip()
        if content:
            results.append((label, content))
            log.debug('Loaded MARCEL.md from %s', path)

    # 1. Home/user level
    home_path = _home_marcelmd()
    _add(home_path, 'user')

    # 2. Walk from filesystem root down to CWD
    for d in _dirs_from_root_to_cwd():
        for filename in _FILENAMES:
            _add(d / filename, 'project')

    return results


def format_marcelmd_for_prompt(files: list[tuple[str, str]]) -> str:
    """Format loaded MARCEL.md files into a string for the system prompt.

    Files are concatenated separated by a horizontal rule.  The caller is
    responsible for adding a section header.
    """
    if not files:
        return ''
    parts = [content for _, content in files]
    return '\n\n---\n\n'.join(parts)
