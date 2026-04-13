"""AI-driven memory selection for Marcel — select relevant memories for context.

Instead of loading all memory files into the system prompt, this module:
1. Scans frontmatter headers (filename, type, description, age)
2. Asks a fast model (Haiku) to pick the most relevant files
3. Loads only the selected files in full

Inspired by Claude Code's findRelevantMemories.ts pattern.
"""

from __future__ import annotations

import json
import logging

from pydantic_ai import Agent

from marcel_core.storage.memory import (
    MemoryHeader,
    format_memory_manifest,
    human_age,
    load_memory_file,
    memory_freshness_note,
    scan_memory_headers,
)

log = logging.getLogger(__name__)

_SELECT_SYSTEM_PROMPT = """\
You are selecting memories that will be useful to Marcel as it processes a \
user's message. You will be given the user's message and a list of available \
memory files with their filenames, types, ages, and descriptions.

Return a JSON array of filenames for the memories that will clearly be useful \
(up to 8). Only include memories you are certain will be helpful based on \
their name and description.

If none are relevant, return an empty array: []

Return ONLY the JSON array, no other text."""

_SELECTOR_MODEL = 'anthropic:claude-haiku-4-5-20251001'

# Maximum memories to select per query
MAX_SELECTED = 8

# If there are fewer than this many memory files, skip the side-query
# and just load them all (not worth the API call)
SELECTION_THRESHOLD = 10


async def select_relevant_memories(
    user_slug: str,
    query: str,
    include_household: bool = True,
) -> list[tuple[MemoryHeader, str]]:
    """Select and load the most relevant memories for a query.

    Uses AI-driven selection via Haiku to pick the top N most relevant memories.

    Args:
        user_slug: The user's slug.
        query: The user's message or context string.
        include_household: If True, also scan `_household` pseudo-user memories.

    Returns:
        List of (header, full_content_with_freshness_note) tuples.
    """
    headers = scan_memory_headers(user_slug)
    if include_household:
        headers += scan_memory_headers('_household')

    if not headers:
        return []

    # For small memory sets, skip the selection query and load everything
    if len(headers) <= SELECTION_THRESHOLD:
        selected = headers
    else:
        selected = await _select_via_model(query, headers)

    # Load full content for selected files
    results: list[tuple[MemoryHeader, str]] = []
    for header in selected:
        topic = header.filename.removesuffix('.md')
        # Determine which user slug to load from based on filepath
        # filepath is <root>/users/<slug>/memory/<file>.md
        slug = header.filepath.parent.parent.name
        content = load_memory_file(slug, topic)
        if not content.strip():
            continue
        # Build a labeled block: ### header + content + optional freshness warning
        label = _format_memory_label(header)
        freshness = memory_freshness_note(header.mtime)
        if freshness:
            content = f'{content.rstrip()}\n\n{freshness}'
        content = f'{label}\n{content}'
        results.append((header, content))

    return results


def _format_memory_label(header: MemoryHeader) -> str:
    """Format a ``### [type] name (age)`` header for a memory block."""
    parts: list[str] = ['###']
    if header.type:
        parts.append(f'[{header.type.value}]')
    parts.append(header.name or header.filename.removesuffix('.md'))
    parts.append(f'({human_age(header.mtime)})')
    return ' '.join(parts)


async def _select_via_model(
    query: str,
    headers: list[MemoryHeader],
) -> list[MemoryHeader]:
    """Ask a fast model to pick the most relevant memory files."""
    manifest = format_memory_manifest(headers)
    prompt = f'User message: {query}\n\nAvailable memories:\n{manifest}'

    # Create a simple pydantic-ai agent for selection
    selector_agent: Agent[None, str] = Agent(
        _SELECTOR_MODEL,
        system_prompt=_SELECT_SYSTEM_PROMPT,
        retries=1,
    )

    try:
        result = await selector_agent.run(prompt)
        response = result.output.strip()
        filenames = _parse_selection(response)

        # Map back to headers
        by_name = {h.filename: h for h in headers}
        selected = [by_name[f] for f in filenames if f in by_name]
        return selected[:MAX_SELECTED]

    except Exception:
        log.exception('Memory selection side-query failed, falling back to all')
        return headers[:MAX_SELECTED]


def _parse_selection(response: str) -> list[str]:
    """Parse a JSON array of filenames from the selector model's response."""
    # Strip markdown code fences if present
    response = response.strip()
    if response.startswith('```'):
        lines = response.split('\n')
        response = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    try:
        result = json.loads(response)
        if isinstance(result, list):
            return [str(f) for f in result if isinstance(f, str)]
    except json.JSONDecodeError:
        log.warning('Memory selector returned non-JSON: %s', response[:200])
    return []
