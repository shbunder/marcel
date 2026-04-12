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
import re
from pathlib import Path

log = logging.getLogger(__name__)


_LEADING_H1_RE = re.compile(r'\A\s*#[^#\n][^\n]*\n+')
# Matches a blockquote paragraph (one or more consecutive `> ...` lines)
# anywhere in the body, paired with its trailing blank line(s).
_BLOCKQUOTE_PARAGRAPH_RE = re.compile(r'(?:^|\n\n)((?:>[^\n]*\n?)+)(?=\n|\Z)')
_CHANNEL_PREAMBLE_RE = re.compile(
    r'\A\s*You are responding via(?: the)? \w+(?: channel)?\.\s*\n+',
    re.IGNORECASE,
)


def _strip_leading_h1(body: str) -> str:
    """Remove a leading ``# Heading`` line and any following blank lines.

    Lets markdown files keep their natural on-disk H1 while the prompt
    builder wraps the body under its own chosen H1.
    """
    return _LEADING_H1_RE.sub('', body, count=1).lstrip('\n')


def _strip_self_ref_blockquote(body: str) -> str:
    """Strip any self-referential blockquote paragraph from *body*.

    Finds a blockquote paragraph (``> …`` lines) anywhere in the text and
    removes it if it mentions ``per-user instructions`` or ``this file`` —
    the giveaway that it's dev documentation, not model context. Other
    blockquotes (user quotes, actual citations) are left alone.

    The blockquote does not need to be at the very start of the body — it
    is common for MARCEL.md to open with a one-line intro followed by a
    self-ref blockquote before the first H2.
    """
    result = body
    for match in list(_BLOCKQUOTE_PARAGRAPH_RE.finditer(body)):
        quote = match.group(1).lower()
        if 'per-user instructions' in quote or 'this file' in quote:
            # Remove the blockquote and collapse surrounding whitespace.
            start, end = match.span(1)
            result = (result[: match.start()] + '\n\n' + result[end:]).strip()
            # Re-run on the cleaned result in case multiple blockquotes exist.
            return _strip_self_ref_blockquote(result)
    return result


def _strip_channel_preamble(body: str) -> str:
    """Strip a leading ``You are responding via <channel>.`` line.

    The ``# Telegram`` (or similar) H1 wrapper makes this preamble
    redundant, so it is dropped at load time.
    """
    return _CHANNEL_PREAMBLE_RE.sub('', body, count=1).lstrip('\n')


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

    Each file is cleaned at load time:
    - leading H1 is stripped (the prompt builder supplies its own wrapper)
    - leading self-referential blockquote is stripped (dev docs, not model context)

    Files are concatenated separated by a horizontal rule.
    """
    if not files:
        return ''
    parts: list[str] = []
    for _label, content in files:
        cleaned = _strip_leading_h1(content)
        cleaned = _strip_self_ref_blockquote(cleaned)
        cleaned = cleaned.strip()
        if cleaned:
            parts.append(cleaned)
    if not parts:
        return ''
    return '\n\n---\n\n'.join(parts)
