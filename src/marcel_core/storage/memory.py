"""Memory storage: load, save, scan, and index topic-scoped memory files.

Memory files use YAML frontmatter for typed metadata::

    ---
    name: dentist_appointment
    description: Dentist appointment on April 15 at 3pm
    type: schedule
    expires: 2026-04-15
    confidence: told
    ---

    Dentist appointment on April 15 at 3pm with Dr. Patel.

Supported types: schedule, preference, person, reference, household.
"""

from __future__ import annotations

import pathlib
import re
import time
from dataclasses import dataclass
from enum import Enum

from ._atomic import atomic_write
from ._root import data_root

# ---------------------------------------------------------------------------
# Memory types
# ---------------------------------------------------------------------------


class MemoryType(str, Enum):
    """Memory taxonomy for a personal assistant context."""

    SCHEDULE = 'schedule'
    PREFERENCE = 'preference'
    PERSON = 'person'
    REFERENCE = 'reference'
    HOUSEHOLD = 'household'


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_KV_RE = re.compile(r'^(\w+)\s*:\s*(.+)$', re.MULTILINE)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-style frontmatter from a memory file.

    Returns:
        (metadata dict, body text after frontmatter).
        If no frontmatter is found, returns ({}, full text).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw_fm = match.group(1)
    body = text[match.end() :]
    metadata = {m.group(1): m.group(2).strip() for m in _KV_RE.finditer(raw_fm)}
    return metadata, body


def parse_memory_type(raw: str | None) -> MemoryType | None:
    """Parse a raw string into a MemoryType, or None if invalid."""
    if not raw:
        return None
    try:
        return MemoryType(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# MemoryHeader — lightweight scan result
# ---------------------------------------------------------------------------


@dataclass
class MemoryHeader:
    """Frontmatter metadata from a memory file, without the full body."""

    filename: str
    filepath: pathlib.Path
    mtime: float
    name: str | None = None
    description: str | None = None
    type: MemoryType | None = None
    expires: str | None = None
    confidence: str | None = None


# ---------------------------------------------------------------------------
# Staleness helpers
# ---------------------------------------------------------------------------


def memory_age_days(mtime: float) -> int:
    """Days elapsed since mtime. 0 for today, 1 for yesterday, etc."""
    return max(0, int((time.time() - mtime) / 86_400))


def memory_freshness_note(mtime: float) -> str:
    """Human-readable staleness warning. Empty for memories ≤ 1 day old."""
    days = memory_age_days(mtime)
    if days <= 1:
        return ''
    if days > 90:
        return f'⚠ This memory is {days} days old and may be very outdated. Verify before relying on it.'
    return f'This memory is {days} days old. It may be outdated — verify if acting on it.'


# ---------------------------------------------------------------------------
# Scanning — read frontmatter headers without loading full content
# ---------------------------------------------------------------------------

_MAX_FRONTMATTER_BYTES = 2048  # read at most 2KB to find frontmatter


def scan_memory_headers(slug: str) -> list[MemoryHeader]:
    """Scan all .md files in a user's memory dir, returning frontmatter headers.

    Only reads the first ~2KB of each file (enough for frontmatter).
    Sorted newest-first by mtime.
    """
    mem_dir = _memory_dir(slug)
    if not mem_dir.exists():
        return []

    headers: list[MemoryHeader] = []
    for entry in mem_dir.iterdir():
        if not entry.is_file() or not entry.name.endswith('.md') or entry.name == 'index.md':
            continue
        try:
            stat = entry.stat()
            raw = entry.read_bytes()[:_MAX_FRONTMATTER_BYTES].decode('utf-8', errors='replace')
            metadata, _ = parse_frontmatter(raw)
            headers.append(
                MemoryHeader(
                    filename=entry.name,
                    filepath=entry,
                    mtime=stat.st_mtime,
                    name=metadata.get('name'),
                    description=metadata.get('description'),
                    type=parse_memory_type(metadata.get('type')),
                    expires=metadata.get('expires'),
                    confidence=metadata.get('confidence'),
                )
            )
        except OSError:
            continue

    headers.sort(key=lambda h: h.mtime, reverse=True)
    return headers


def format_memory_manifest(headers: list[MemoryHeader]) -> str:
    """Format memory headers as a text manifest for prompt injection.

    One line per file: ``- [type] filename (age): description``
    """
    lines: list[str] = []
    for h in headers:
        tag = f'[{h.type.value}] ' if h.type else ''
        age = _human_age(h.mtime)
        desc = f': {h.description}' if h.description else ''
        lines.append(f'- {tag}{h.filename} ({age}){desc}')
    return '\n'.join(lines)


def _human_age(mtime: float) -> str:
    days = memory_age_days(mtime)
    if days == 0:
        return 'today'
    if days == 1:
        return 'yesterday'
    return f'{days} days ago'


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _memory_dir(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug / 'memory'


def _memory_path(slug: str, topic: str) -> pathlib.Path:
    """Return the path for a memory topic file.

    ``topic`` is the filename without ``.md``, e.g. ``"calendar"``.
    """
    filename = topic if topic.endswith('.md') else f'{topic}.md'
    return _memory_dir(slug) / filename


def _index_path(slug: str) -> pathlib.Path:
    return _memory_dir(slug) / 'index.md'


# ---------------------------------------------------------------------------
# CRUD operations (unchanged from original API)
# ---------------------------------------------------------------------------


def load_memory_index(slug: str) -> str:
    """Return the raw markdown content of the memory index."""
    path = _index_path(slug)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def load_memory_file(slug: str, topic: str) -> str:
    """Return the raw markdown content of a memory topic file."""
    path = _memory_path(slug, topic)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def save_memory_file(slug: str, topic: str, content: str) -> None:
    """Persist a memory topic file. Creates directory if needed."""
    path = _memory_path(slug, topic)
    atomic_write(path, content)


def update_memory_index(slug: str, topic: str, description: str) -> None:
    """Add a topic entry to the memory index if not already present."""
    path = _index_path(slug)
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    filename = topic if topic.endswith('.md') else f'{topic}.md'
    if f'[{filename}]' in existing:
        return
    entry = f'- [{filename}]({filename}) — {description}\n'
    updated = existing + entry
    atomic_write(path, updated)
