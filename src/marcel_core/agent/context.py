"""Builds the system prompt for a ClaudeSDKClient session.

The system prompt is set once when the session connects.  Conversation history
is maintained by the SDK internally — we no longer inject it here.

Memory loading is a full dump for now; ISSUE-024 Part C will replace this with
relevance-based selection.
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
) -> str:
    """Assemble the system prompt for a new ClaudeSDKClient session.

    Args:
        user_slug: The user's slug (directory name under data/users/).
        channel: The originating channel (cli, app, ios, telegram).

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
