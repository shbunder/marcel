"""Integration dispatcher tool for Marcel.

Exposes a single pydantic-ai tool that dispatches to the skills registry.
This preserves the @register decorator pattern while keeping tool count minimal.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.skills.executor import run
from marcel_core.skills.registry import get_skill, list_skills

log = logging.getLogger(__name__)


async def integration(
    ctx: RunContext[MarcelDeps],
    skill: str,
    params: dict[str, str] | None = None,
) -> str:
    """Execute a registered integration skill.

    Integrations are external services that Marcel can call: calendar, banking,
    smart home, etc. Each integration is documented in .marcel/skills/{name}/SKILL.md.

    Available skills:
    - banking.setup: Initialize KBC banking integration
    - banking.accounts: List bank accounts
    - banking.balance: Get account balance
    - banking.transactions: Fetch recent transactions
    - banking.sync: Force sync of banking data
    - icloud.calendar: Access iCloud calendar events
    - icloud.mail: Check iCloud mail

    Args:
        ctx: Agent context with user information.
        skill: The skill name (e.g., "banking.balance").
        params: Skill-specific parameters (see SKILL.md for each integration).

    Returns:
        Result string from the integration.
    """
    log.info('[integration] user=%s skill=%s', ctx.deps.user_slug, skill)

    if params is None:
        params = {}

    try:
        config = get_skill(skill)
    except KeyError as exc:
        available = list_skills()
        return f'Error: {exc}\n\nAvailable skills: {", ".join(available)}'

    try:
        result = await run(config, params, ctx.deps.user_slug)
        return result
    except Exception as exc:
        log.exception('[integration] Skill execution failed')
        return f'Error executing {skill}: {exc}'


async def memory_search(
    ctx: RunContext[MarcelDeps],
    query: str,
    type_filter: str | None = None,
    max_results: int = 10,
) -> str:
    """Search across memory files by keyword.

    Use this when pre-loaded memories are not enough and you need to find
    specific information (e.g., a past appointment, a preference, a person).

    Args:
        ctx: Agent context.
        query: Search query (matches against memory names, descriptions, content).
        type_filter: Optional type filter: "schedule", "preference", "person", "reference", "household".
        max_results: Maximum number of results to return (default: 10).

    Returns:
        Matching memory files with snippets.
    """
    from marcel_core.storage.memory import MemoryType, search_memory_files

    log.info('[memory_search] user=%s query=%s', ctx.deps.user_slug, query)

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
        max_results=max_results,
    )

    if not results:
        return f'No memories found matching "{query}".'

    lines: list[str] = []
    for r in results:
        tag = f'[{r.type.value}] ' if r.type else ''
        desc = f' — {r.description}' if r.description else ''
        lines.append(f'### {tag}{r.filename}{desc}')
        if r.snippet:
            lines.append(r.snippet)
        lines.append('')

    return '\n'.join(lines).strip()


async def conversation_search(
    ctx: RunContext[MarcelDeps],
    query: str,
    max_results: int = 5,
) -> str:
    """Search past conversation history by keyword.

    Use this when you need to recall a past discussion, find something the user
    mentioned before, or look up context from earlier in the conversation.

    This searches across all sealed (summarized) conversation segments, not just
    the current active segment. Returns matching messages with surrounding context.

    Examples:
    - User: "Remember when we talked about the dentist?"
    - User: "What was that restaurant you recommended?"
    - User: "What did we decide about the kitchen renovation?"

    Args:
        ctx: Agent context.
        query: Search query (keywords to match against conversation history).
        max_results: Maximum number of results to return (default: 5).

    Returns:
        Matching conversation excerpts with surrounding context.
    """
    from marcel_core.memory.conversation import search_conversations

    log.info('[conversation_search] user=%s query=%s', ctx.deps.user_slug, query)

    results = search_conversations(
        ctx.deps.user_slug,
        ctx.deps.channel,
        query,
        max_results=max_results,
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
            marker = '→ ' if msg.text and query.lower() in msg.text.lower() else '  '
            lines.append(f'{marker}{role}: {text}')
        lines.append('')

    return '\n'.join(lines).strip()


async def compact_now(ctx: RunContext[MarcelDeps]) -> str:
    """Manually compress the current conversation segment into a summary.

    Use this when the conversation topic has shifted significantly, the context
    feels cluttered, or the user explicitly asks to compress/compact the conversation.

    This seals the current segment, generates a summary via a fast model, and
    opens a new active segment. The summary becomes part of the rolling context.

    Returns:
        Confirmation with what was preserved in the summary.
    """
    from marcel_core.memory.summarizer import summarize_active_segment

    log.info('[compact_now] user=%s channel=%s', ctx.deps.user_slug, ctx.deps.channel)

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
                f'({summary.time_span_from.strftime("%H:%M")}–{summary.time_span_to.strftime("%H:%M")}):\n\n'
                f'{summary.summary}'
            )
        return 'Conversation compressed successfully.'

    return 'Nothing to compress — the current conversation segment is empty or compaction failed.'


async def notify(ctx: RunContext[MarcelDeps], message: str) -> str:
    """Send a short progress update to the user mid-task.

    Use this to keep the user informed during long operations. Always call this
    at the start of any multi-step task and after each major step.

    Examples:
    - "On it..."
    - "Creating issue..."
    - "Running tests..."
    - "Pushing to remote..."

    Args:
        ctx: Agent context.
        message: Short progress message (1-2 sentences).

    Returns:
        Confirmation or error message.
    """
    log.info('[notify] user=%s channel=%s msg=%s', ctx.deps.user_slug, ctx.deps.channel, message)

    if not message:
        return 'ok'

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
            log.warning('[notify] Telegram notification failed: %s', exc)
            return f'notify failed: {exc}'

    # For other channels, just log (they'll see it in the response stream)
    return 'ok'
