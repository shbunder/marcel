"""Context and dependencies for Marcel agents — pydantic-ai deps_type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarcelDeps:
    """Dependencies injected into Marcel agent tools via RunContext.

    This is the deps_type for pydantic-ai Agent. Tools receive RunContext[MarcelDeps]
    which provides access to user context, conversation state, and channel information.
    """

    user_slug: str
    """The user's slug identifier (directory name under ~/.marcel/users/)."""

    conversation_id: str
    """The active conversation identifier."""

    channel: str
    """The originating channel: 'cli', 'telegram', 'app', 'ios', 'websocket'."""

    model: str | None = None
    """Optional model override (e.g., 'anthropic:claude-opus-4-6')."""


async def build_instructions_async(deps: MarcelDeps, query: str = '') -> str:
    """Build dynamic system instructions with AI-selected memories.

    This is an async version that can call the memory selector.

    Args:
        deps: The MarcelDeps context.
        query: The user's query (for memory selection).

    Returns:
        Complete system prompt string with selected memories.
    """
    from marcel_core.memory.selector import select_relevant_memories
    from marcel_core.storage import load_user_profile

    profile = load_user_profile(deps.user_slug)

    # Select relevant memories if we have a query
    memory_content = ''
    if query:
        try:
            selected_memories = await select_relevant_memories(deps.user_slug, query)
            if selected_memories:
                memory_parts = [content for _, content in selected_memories]
                memory_content = '\n\n---\n\n'.join(memory_parts)
        except Exception as exc:
            # Fall back gracefully if memory selection fails
            import logging

            logging.getLogger(__name__).warning('Memory selection failed: %s', exc)

    # Channel-specific formatting hints
    channel_hints = {
        'cli': 'Use rich markdown: headers, bold, code blocks, and bullet lists freely.',
        'app': 'Use full markdown. You may include structured data for card rendering.',
        'ios': 'Use markdown. Keep responses concise for mobile screens.',
        'telegram': (
            'Use standard markdown (bold, italic, code, code blocks, links, lists, headers, blockquotes). '
            'Do NOT use Telegram MarkdownV2 escape syntax — output will be converted server-side. '
            'IMPORTANT: For any task that takes more than one step, call the notify tool '
            'at the start ("On it...") and after each major step so the user always knows '
            'what you are doing. Never go silent for more than a few seconds.'
        ),
        'websocket': 'Use rich markdown. Streaming is supported, so you can send progressive updates.',
    }

    format_hint = channel_hints.get(deps.channel, channel_hints['cli'])

    lines = [
        f'You are Marcel, a warm and capable personal assistant for {deps.user_slug}.',
        '',
        f'## What you know about {deps.user_slug}',
        profile or '(no profile information yet)',
        '',
    ]

    if memory_content:
        lines += ['## Memory', memory_content, '']

    lines += [
        '## Channel',
        f'You are responding via the {deps.channel} channel. {format_hint}',
    ]

    return '\n'.join(lines)


def build_instructions(deps: MarcelDeps) -> str:
    """Build dynamic system instructions for a Marcel agent.

    Called by pydantic-ai Agent when instructions parameter is a callable.
    This is the sync version used during agent initialization.

    Args:
        deps: The MarcelDeps context.

    Returns:
        Complete system prompt string (without AI-selected memories).
    """
    from marcel_core.storage import load_user_profile

    profile = load_user_profile(deps.user_slug)

    # Channel-specific formatting hints
    channel_hints = {
        'cli': 'Use rich markdown: headers, bold, code blocks, and bullet lists freely.',
        'app': 'Use full markdown. You may include structured data for card rendering.',
        'ios': 'Use markdown. Keep responses concise for mobile screens.',
        'telegram': (
            'Use standard markdown (bold, italic, code, code blocks, links, lists, headers, blockquotes). '
            'Do NOT use Telegram MarkdownV2 escape syntax — output will be converted server-side. '
            'IMPORTANT: For any task that takes more than one step, call the notify tool '
            'at the start ("On it...") and after each major step so the user always knows '
            'what you are doing. Never go silent for more than a few seconds.'
        ),
        'websocket': 'Use rich markdown. Streaming is supported, so you can send progressive updates.',
    }

    format_hint = channel_hints.get(deps.channel, channel_hints['cli'])

    lines = [
        f'You are Marcel, a warm and capable personal assistant for {deps.user_slug}.',
        '',
        f'## What you know about {deps.user_slug}',
        profile or '(no profile information yet)',
        '',
        '## Channel',
        f'You are responding via the {deps.channel} channel. {format_hint}',
    ]

    return '\n'.join(lines)
