"""Builds the system prompt for each agent turn.

Loads user profile, all distilled memory files, and recent conversation history,
then assembles them into a structured prompt injected before each Claude call.
"""

import re

from marcel_core import storage

_CHANNEL_FORMAT: dict[str, str] = {
    'cli': 'Use rich markdown: headers, bold, code blocks, and bullet lists freely.',
    'app': 'Use full markdown. You may include structured data for card rendering.',
    'ios': 'Use markdown. Keep responses concise for mobile screens.',
    'telegram': (
        'Use Telegram MarkdownV2 only. Avoid HTML and unsupported Markdown syntax. '
        'IMPORTANT: For any task that takes more than one step, call the notify tool '
        'at the start ("On it...") and after each major step so the user always knows '
        'what you are doing. Never go silent for more than a few seconds.'
    ),
}


def build_system_prompt(
    user_slug: str,
    channel: str,
    conversation_id: str | None = None,
) -> str:
    """Assemble the system prompt for one agent turn.

    Args:
        user_slug: The user's slug (directory name under data/users/).
        channel: The originating channel (cli, app, ios, telegram).
        conversation_id: Filename stem of the current conversation, or None for new.

    Returns:
        A complete system prompt string ready to pass to ClaudeAgentOptions.
    """
    profile = storage.load_user_profile(user_slug)
    memory_content = _load_all_memory(user_slug)

    lines: list[str] = [
        f'You are Marcel, a warm and capable personal assistant for {user_slug}.',
        '',
        f'## What you know about {user_slug}',
        profile or '(no profile information yet)',
        '',
    ]

    if memory_content:
        lines += ['## Memory', memory_content, '']

    if conversation_id:
        history = storage.load_conversation(user_slug, conversation_id)
        if history.strip():
            lines += ['## Recent conversation', history, '']

    format_hint = _CHANNEL_FORMAT.get(channel, _CHANNEL_FORMAT['cli'])
    lines += [
        '## Channel',
        f'You are responding via the {channel} channel. {format_hint}',
    ]

    return '\n'.join(lines)


def _load_all_memory(user_slug: str) -> str:
    """Load and concatenate all topic memory files referenced in the memory index."""
    index = storage.load_memory_index(user_slug)
    if not index.strip():
        return ''

    # Match link text filenames: [calendar.md](calendar.md)
    filenames = re.findall(r'\[([^\]]+\.md)\]', index)
    parts: list[str] = []
    for filename in filenames:
        topic = filename.removesuffix('.md')
        content = storage.load_memory_file(user_slug, topic)
        if content.strip():
            parts.append(content)

    return '\n\n'.join(parts)
