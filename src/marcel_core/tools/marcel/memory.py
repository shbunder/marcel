"""Memory-related actions for the ``marcel`` tool."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def search_memory(
    ctx: RunContext[MarcelDeps],
    query: str | None,
    type_filter: str | None,
    max_results: int | None,
) -> str:
    """Search across memory files by keyword."""
    if not query:
        return 'Error: query= is required for search_memory action.'

    from marcel_core.storage.memory import MemoryType, search_memory_files

    log.info('[marcel:search_memory] user=%s query=%s', ctx.deps.user_slug, query)

    type_obj = None
    if type_filter:
        try:
            type_obj = MemoryType(type_filter)
        except ValueError:
            valid_types = ', '.join(t.value for t in MemoryType)
            return f'Error: Invalid type filter "{type_filter}". Valid types: {valid_types}'

    results = search_memory_files(
        ctx.deps.user_slug,
        query,
        type_filter=type_obj,
        max_results=max_results or 10,
    )

    if not results:
        return f'No memories found matching "{query}".'

    lines: list[str] = []
    for r in results:
        tag = f'[{r.type.value}] ' if r.type else ''
        desc = f' \u2014 {r.description}' if r.description else ''
        lines.append(f'### {tag}{r.filename}{desc}')
        if r.snippet:
            lines.append(r.snippet)
        lines.append('')

    return '\n'.join(lines).strip()


def save_memory(ctx: RunContext[MarcelDeps], name: str | None, content: str | None) -> str:
    """Save a memory file directly. name= is the filename, message= is the content."""
    if not name:
        return 'Error: name= is required for save_memory (the filename, e.g. "coffee_preference.md").'
    if not content:
        return 'Error: message= is required for save_memory (the full file content including YAML frontmatter).'

    from marcel_core.storage.memory import parse_frontmatter, save_memory_file, update_memory_index

    # Sanitize filename
    safe_name = Path(name).name
    if not safe_name.endswith('.md'):
        safe_name += '.md'
    topic = safe_name.removesuffix('.md')

    log.info('[marcel:save_memory] user=%s file=%s', ctx.deps.user_slug, safe_name)
    save_memory_file(ctx.deps.user_slug, topic, content)

    # Extract description from frontmatter for the index
    metadata, _ = parse_frontmatter(content)
    description = metadata.get('description', topic.replace('_', ' '))
    update_memory_index(ctx.deps.user_slug, topic, description)

    return f'Saved memory file: {safe_name}'
