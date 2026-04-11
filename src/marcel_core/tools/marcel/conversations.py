"""Conversation history and compaction actions for the ``marcel`` tool."""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def search_conversations(
    ctx: RunContext[MarcelDeps],
    query: str | None,
    max_results: int | None,
) -> str:
    """Search past conversation history by keyword."""
    if not query:
        return 'Error: query= is required for search_conversations action.'

    from marcel_core.memory.conversation import search_conversations as _search

    log.info('[marcel:search_conversations] user=%s query=%s', ctx.deps.user_slug, query)

    results = _search(
        ctx.deps.user_slug,
        ctx.deps.channel,
        query,
        max_results=max_results or 5,
    )

    if not results:
        return f'No past conversation found matching "{query}".'

    lines: list[str] = []
    for entry, context_msgs in results:
        lines.append(f'### Match in {entry.segment} ({entry.timestamp[:10]})')
        for msg in context_msgs:
            role = 'User' if msg.role == 'user' else 'Marcel' if msg.role == 'assistant' else msg.role
            text = msg.text or '(no text)'
            if len(text) > 300:
                text = text[:300] + '...'
            marker = '\u2192 ' if msg.text and query.lower() in msg.text.lower() else '  '
            lines.append(f'{marker}{role}: {text}')
        lines.append('')

    return '\n'.join(lines).strip()


async def compact(ctx: RunContext[MarcelDeps]) -> str:
    """Compress the current conversation segment into a summary."""
    from marcel_core.memory.conversation import load_latest_summary
    from marcel_core.memory.summarizer import summarize_active_segment

    log.info('[marcel:compact] user=%s channel=%s', ctx.deps.user_slug, ctx.deps.channel)

    success = await summarize_active_segment(
        ctx.deps.user_slug,
        ctx.deps.channel,
        trigger='manual',
    )

    if success:
        summary = load_latest_summary(ctx.deps.user_slug, ctx.deps.channel)
        if summary:
            return (
                f'Conversation compressed. Summary of {summary.message_count} messages '
                f'({summary.time_span_from.strftime("%H:%M")}\u2013{summary.time_span_to.strftime("%H:%M")}):\n\n'
                f'{summary.summary}'
            )
        return 'Conversation compressed successfully.'

    return 'Nothing to compress \u2014 the current conversation segment is empty or compaction failed.'
