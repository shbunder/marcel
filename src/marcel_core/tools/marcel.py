"""Unified internal utilities tool for Marcel.

Consolidates Marcel's self-management operations into a single tool:
read_skill, search_memory, search_conversations, compact, and notify.

External capabilities (browser, bash, file I/O, charts) and integration
dispatch remain as separate tools.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def marcel(
    ctx: RunContext[MarcelDeps],
    action: str,
    name: str | None = None,
    query: str | None = None,
    message: str | None = None,
    type_filter: str | None = None,
    max_results: int | None = None,
) -> str:
    """Marcel's internal utilities for managing skills, memory, and conversations.

    Actions:
      read_skill           Load full documentation for a skill (name= required).
      search_memory        Search memory files by keyword (query= required).
      search_conversations Search past conversation history (query= required).
      compact              Compress current conversation segment into a summary.
      notify               Send a progress update to the user (message= required).

    Args:
        ctx: Agent context with user and conversation info.
        action: The action to perform (see above).
        name: Skill name for read_skill action.
        query: Search query for search_memory / search_conversations.
        message: Progress message for notify action.
        type_filter: Optional memory type filter for search_memory.
        max_results: Max results for search actions (default: 10 for memory, 5 for conversations).

    Returns:
        Action result string.
    """
    match action:
        case 'read_skill':
            return await _read_skill(ctx, name)
        case 'search_memory':
            return await _search_memory(ctx, query, type_filter, max_results)
        case 'search_conversations':
            return await _search_conversations(ctx, query, max_results)
        case 'compact':
            return await _compact(ctx)
        case 'notify':
            return await _notify(ctx, message)
        case _:
            return (
                f'Unknown action: {action!r}. '
                f'Available: read_skill, search_memory, search_conversations, compact, notify'
            )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _read_skill(ctx: RunContext[MarcelDeps], name: str | None) -> str:
    """Load full documentation for a skill by name."""
    if not name:
        return 'Error: name= is required for read_skill action.'

    from marcel_core.skills.loader import get_skill_content, load_skills

    log.info('[marcel:read_skill] user=%s skill=%s', ctx.deps.user_slug, name)

    content = get_skill_content(name, ctx.deps.user_slug)
    if content is None:
        available = [s.name for s in load_skills(ctx.deps.user_slug)]
        return f'Unknown skill: {name!r}. Available skills: {", ".join(available)}'

    # Track that this skill has been read (prevents duplicate auto-inject in integration tool)
    ctx.deps.read_skills.add(name)
    return content


async def _search_memory(
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


async def _search_conversations(
    ctx: RunContext[MarcelDeps],
    query: str | None,
    max_results: int | None,
) -> str:
    """Search past conversation history by keyword."""
    if not query:
        return 'Error: query= is required for search_conversations action.'

    from marcel_core.memory.conversation import search_conversations

    log.info('[marcel:search_conversations] user=%s query=%s', ctx.deps.user_slug, query)

    results = search_conversations(
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


async def _compact(ctx: RunContext[MarcelDeps]) -> str:
    """Compress the current conversation segment into a summary."""
    from marcel_core.memory.summarizer import summarize_active_segment

    log.info('[marcel:compact] user=%s channel=%s', ctx.deps.user_slug, ctx.deps.channel)

    success = await summarize_active_segment(
        ctx.deps.user_slug,
        ctx.deps.channel,
        trigger='manual',
    )

    if success:
        from marcel_core.memory.conversation import load_latest_summary

        summary = load_latest_summary(ctx.deps.user_slug, ctx.deps.channel)
        if summary:
            return (
                f'Conversation compressed. Summary of {summary.message_count} messages '
                f'({summary.time_span_from.strftime("%H:%M")}\u2013{summary.time_span_to.strftime("%H:%M")}):\n\n'
                f'{summary.summary}'
            )
        return 'Conversation compressed successfully.'

    return 'Nothing to compress \u2014 the current conversation segment is empty or compaction failed.'


async def send_notify(ctx: RunContext[MarcelDeps], message: str) -> str:
    """Public helper for sending notifications from other tools (e.g. claude_code).

    Unlike the ``_notify`` action handler, this takes a required message string
    and is importable by other modules.
    """
    return await _notify(ctx, message)


async def _notify(ctx: RunContext[MarcelDeps], message: str | None) -> str:
    """Send a short progress update to the user mid-task."""
    if not message:
        return 'ok'

    log.info('[marcel:notify] user=%s channel=%s msg=%s', ctx.deps.user_slug, ctx.deps.channel, message)

    # For Telegram, send real-time notification
    if ctx.deps.channel == 'telegram':
        try:
            from marcel_core.channels.telegram import bot, sessions
            from marcel_core.channels.telegram.formatting import escape_html

            chat_id = sessions.get_chat_id(ctx.deps.user_slug)
            if chat_id:
                await bot.send_message(int(chat_id), escape_html(message))
                return 'ok'
        except Exception as exc:
            log.warning('[marcel:notify] Telegram notification failed: %s', exc)
            return f'notify failed: {exc}'

    # For other channels, just log (they'll see it in the response stream)
    return 'ok'
