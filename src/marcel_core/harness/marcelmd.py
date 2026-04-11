"""MARCEL.md loader — discover and load personal assistant instruction files.

MARCEL.md files provide instructions to Marcel when acting as a personal
assistant, separate from the developer-focused CLAUDE.md files that govern
code changes (which are read by the inner Claude Code loop).

## Loading order (lowest to highest priority)

1. ``<data_root>/MARCEL.md`` — global instructions for all users.
2. ``<data_root>/users/<slug>/MARCEL.md`` — per-user instructions (who this user
   is, preferences, household role).

All files live under the data root (``~/.marcel/`` or ``$MARCEL_DATA_DIR``).
They are concatenated; later files take precedence in models that weight
later context more.

## Separation from CLAUDE.md

CLAUDE.md files are read by the inner Claude Code agent when Marcel performs
code changes or self-modification.  MARCEL.md files are read by Marcel in
personal assistant mode.  This keeps the two concerns clearly separated.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _data_root() -> Path:
    """Return the Marcel data root directory (matches storage._root.data_root())."""
    from marcel_core.storage._root import data_root

    return data_root()


def load_marcelmd_files(user_slug: str) -> list[tuple[str, str]]:
    """Discover and load all MARCEL.md files for ``user_slug``.

    Returns a list of ``(label, content)`` pairs in loading order (global
    first, per-user last).  Empty files are skipped.

    Args:
        user_slug: The user slug used to locate per-user instructions.

    Returns:
        List of (label, content) tuples where label is one of
        'global' or 'user'.
    """
    results: list[tuple[str, str]] = []
    seen: set[Path] = set()

    def _add(path: Path, label: str) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            return
        seen.add(resolved)
        if not path.exists():
            return
        content = path.read_text(encoding='utf-8').strip()
        if content:
            results.append((label, content))
            log.debug('Loaded MARCEL.md (%s) from %s', label, path)

    data = _data_root()

    # 1. Global instructions for all users
    _add(data / 'MARCEL.md', 'global')

    # 2. Per-user instructions
    _add(data / 'users' / user_slug / 'MARCEL.md', 'user')

    return results


def format_marcelmd_for_prompt(files: list[tuple[str, str]]) -> str:
    """Format loaded MARCEL.md files into a string for the system prompt.

    Files are concatenated separated by a horizontal rule.
    """
    if not files:
        return ''
    parts = [content for _, content in files]
    return '\n\n---\n\n'.join(parts)
