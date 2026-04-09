"""Builds the system prompt for a ClaudeSDKClient session.

The system prompt is set once when the session connects.  Conversation history
is maintained by the SDK internally — we no longer inject it here.

Memory is loaded from frontmatter-typed files, sorted by recency, with
staleness warnings on old entries.  For small memory sets all files are
included; for large sets only the most recent are loaded in-prompt (the agent
can use the ``memory_search`` tool for older/less-relevant memories).
"""

from marcel_core.skills.loader import format_skills_for_prompt, load_skills
from marcel_core.storage.memory import (
    load_memory_file,
    memory_freshness_note,
    scan_memory_headers,
)

# Maximum memory files to include in the system prompt.
# Beyond this, the agent should use the memory_search tool.
_MAX_MEMORY_FILES = 15

_CHANNEL_FORMAT: dict[str, str] = {
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
    from marcel_core import storage

    profile = storage.load_user_profile(user_slug)
    memory_content = _load_memory(user_slug)
    skills_content = _load_skills(user_slug)

    lines: list[str] = [
        f'You are Marcel, a warm and capable personal assistant for {user_slug}.',
        '',
        f'## What you know about {user_slug}',
        profile or '(no profile information yet)',
        '',
    ]

    if memory_content:
        lines += ['## Memory', memory_content, '']

    if skills_content:
        lines += ['## Skills', skills_content, '']

    format_hint = _CHANNEL_FORMAT.get(channel, _CHANNEL_FORMAT['cli'])
    lines += [
        '## Channel',
        f'You are responding via the {channel} channel. {format_hint}',
    ]

    return '\n'.join(lines)


def _load_skills(user_slug: str) -> str:
    """Load skill docs from .marcel/skills/ directories.

    Reads from both the project directory and the user's home directory,
    with home overriding project.  Skills whose requirements aren't met
    get their SETUP.md fallback instead.
    """
    skills = load_skills(user_slug)
    return format_skills_for_prompt(skills)


def _load_memory(user_slug: str) -> str:
    """Load memory files sorted by recency, with staleness notes.

    Scans frontmatter headers, takes the most recent _MAX_MEMORY_FILES,
    loads their full content, and appends freshness warnings for older entries.
    Also includes ``_household`` shared memories.
    """
    headers = scan_memory_headers(user_slug)
    headers += scan_memory_headers('_household')

    if not headers:
        return ''

    # Sort all by mtime descending (most recent first) and cap.
    headers.sort(key=lambda h: h.mtime, reverse=True)
    headers = headers[:_MAX_MEMORY_FILES]

    parts: list[str] = []
    for header in headers:
        # Determine slug from filepath: <root>/users/<slug>/memory/<file>.md
        slug = header.filepath.parent.parent.name
        topic = header.filename.removesuffix('.md')
        content = load_memory_file(slug, topic)
        if not content.strip():
            continue

        freshness = memory_freshness_note(header.mtime)
        if freshness:
            content = f'{content.rstrip()}\n\n{freshness}'
        parts.append(content)

    return '\n\n---\n\n'.join(parts)
