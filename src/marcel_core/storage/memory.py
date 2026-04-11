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

import datetime
import logging
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
    FEEDBACK = 'feedback'


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
        age = human_age(h.mtime)
        desc = f': {h.description}' if h.description else ''
        lines.append(f'- {tag}{h.filename} ({age}){desc}')
    return '\n'.join(lines)


def human_age(mtime: float) -> str:
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


@dataclass
class MemorySearchResult:
    """A single result from searching memory files."""

    filename: str
    name: str | None
    type: MemoryType | None
    description: str | None
    snippet: str
    mtime: float


def search_memory_files(
    slug: str,
    query: str,
    *,
    type_filter: MemoryType | None = None,
    max_results: int = 10,
    include_household: bool = True,
) -> list[MemorySearchResult]:
    """Search memory files by keyword, returning matching results.

    Searches both frontmatter metadata (name, description) and body content.
    Results are ranked: frontmatter matches first, then body matches, both
    sorted by recency.

    Args:
        slug: User slug to search.
        query: Search query (case-insensitive substring match).
        type_filter: Optional filter by memory type.
        max_results: Maximum results to return.
        include_household: Also search ``_household`` memories.

    Returns:
        List of :class:`MemorySearchResult` ordered by relevance then recency.
    """
    slugs = [slug]
    if include_household:
        slugs.append('_household')

    query_lower = query.lower()
    meta_matches: list[MemorySearchResult] = []
    body_matches: list[MemorySearchResult] = []

    for s in slugs:
        mem_dir = _memory_dir(s)
        if not mem_dir.exists():
            continue
        for entry in mem_dir.iterdir():
            if not entry.is_file() or not entry.name.endswith('.md') or entry.name == 'index.md':
                continue
            try:
                content = entry.read_text(encoding='utf-8')
                stat = entry.stat()
            except OSError:
                continue

            metadata, body = parse_frontmatter(content)
            mem_type = parse_memory_type(metadata.get('type'))

            if type_filter is not None and mem_type != type_filter:
                continue

            name = metadata.get('name')
            description = metadata.get('description')
            filename_stem = entry.name.removesuffix('.md')

            # Check if query matches frontmatter fields or filename.
            meta_hit = False
            if query_lower in filename_stem.lower():
                meta_hit = True
            if name and query_lower in name.lower():
                meta_hit = True
            if description and query_lower in description.lower():
                meta_hit = True

            # Check if query matches body content.
            body_hit = query_lower in body.lower()

            if not meta_hit and not body_hit:
                continue

            snippet = _extract_snippet(body, query_lower)
            result = MemorySearchResult(
                filename=entry.name,
                name=name,
                type=mem_type,
                description=description,
                snippet=snippet,
                mtime=stat.st_mtime,
            )
            if meta_hit:
                meta_matches.append(result)
            else:
                body_matches.append(result)

    # Meta matches first (sorted newest-first), then body matches.
    meta_matches.sort(key=lambda r: r.mtime, reverse=True)
    body_matches.sort(key=lambda r: r.mtime, reverse=True)
    return (meta_matches + body_matches)[:max_results]


def _extract_snippet(body: str, query_lower: str, context_chars: int = 120) -> str:
    """Extract a short snippet around the first match of query in body."""
    body_stripped = body.strip()
    if not body_stripped:
        return ''
    idx = body_stripped.lower().find(query_lower)
    if idx == -1:
        # No match in body; return first N chars.
        return body_stripped[:context_chars].replace('\n', ' ').strip()
    start = max(0, idx - context_chars // 2)
    end = min(len(body_stripped), idx + len(query_lower) + context_chars // 2)
    snippet = body_stripped[start:end].replace('\n', ' ').strip()
    if start > 0:
        snippet = '...' + snippet
    if end < len(body_stripped):
        snippet = snippet + '...'
    return snippet


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


# ---------------------------------------------------------------------------
# Memory lifecycle
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

_INDEX_CAP = 200  # Maximum lines in the memory index file.


def prune_expired_memories(slug: str, today: datetime.date | None = None) -> list[str]:
    """Delete schedule-type memories whose ``expires`` date has passed.

    Args:
        slug: User slug.
        today: Override for "today" (for testing). Defaults to ``date.today()``.

    Returns:
        List of filenames that were pruned.
    """
    if today is None:
        today = datetime.date.today()

    headers = scan_memory_headers(slug)
    pruned: list[str] = []

    for header in headers:
        if header.type != MemoryType.SCHEDULE or not header.expires:
            continue
        try:
            expires_date = datetime.date.fromisoformat(header.expires)
        except ValueError:
            continue
        if expires_date < today:
            try:
                header.filepath.unlink()
                pruned.append(header.filename)
                log.info('Pruned expired memory %s for user=%s (expired %s)', header.filename, slug, header.expires)
            except OSError:
                log.warning('Failed to prune %s for user=%s', header.filename, slug)

    return pruned


def rebuild_memory_index(slug: str) -> None:
    """Rebuild the memory index from actual files on disk.

    Scans all memory files, reads their frontmatter, and writes a fresh
    index. Removes entries for deleted files and adds entries for new ones.
    """
    headers = scan_memory_headers(slug)
    if not headers:
        # No memory files — remove stale index if present
        path = _index_path(slug)
        if path.exists():
            atomic_write(path, '')
        return

    lines: list[str] = []
    for h in headers:
        desc = h.description or h.name or h.filename.removesuffix('.md').replace('_', ' ')
        tag = f'[{h.type.value}] ' if h.type else ''
        lines.append(f'- {tag}[{h.filename}]({h.filename}) — {desc}')

    atomic_write(_index_path(slug), '\n'.join(lines) + '\n')
    log.info('Rebuilt memory index for user=%s (%d entries)', slug, len(lines))


def enforce_index_cap(slug: str, max_lines: int = _INDEX_CAP) -> bool:
    """Truncate the memory index to ``max_lines``, appending a warning if needed.

    Returns:
        True if the index was truncated, False otherwise.
    """
    path = _index_path(slug)
    if not path.exists():
        return False

    content = path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)

    if len(lines) <= max_lines:
        return False

    truncated = lines[:max_lines]
    warning = f'\n<!-- Index truncated at {max_lines} lines. Older entries removed. -->\n'
    atomic_write(path, ''.join(truncated) + warning)
    log.info('Truncated memory index for user=%s from %d to %d lines', slug, len(lines), max_lines)
    return True
