"""Context and dependencies for Marcel agents — pydantic-ai deps_type."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from pydantic import dataclasses as pydantic_dc

log = logging.getLogger(__name__)

# Path to bundled default channel prompt files.
_DEFAULTS_CHANNELS = Path(__file__).resolve().parent.parent / 'defaults' / 'channels'


@pydantic_dc.dataclass
class MarcelDeps:
    """Dependencies injected into Marcel agent tools via RunContext.

    This is the deps_type for pydantic-ai Agent. Tools receive RunContext[MarcelDeps]
    which provides access to user context, conversation state, and channel information.

    Uses ``pydantic.dataclasses.dataclass`` for validation while keeping the
    dataclass-style API that pydantic-ai expects for ``deps_type``.
    """

    user_slug: str
    """The user's slug identifier (directory name under ~/.marcel/users/)."""

    conversation_id: str
    """The active conversation identifier."""

    channel: str
    """The originating channel: 'cli', 'telegram', 'app', 'ios', 'websocket'."""

    model: str | None = None
    """Optional model override (e.g., 'claude-opus-4-6', 'gpt-4')."""

    role: str = 'user'
    """The user's role: 'admin' or 'user'."""

    cwd: str | None = None
    """Working directory for bash and file operations.

    For admin CLI sessions this is the directory where the CLI was invoked.
    For admin non-CLI sessions this defaults to the user's home directory.
    None falls back to the project root.
    """

    read_skills: set[str] = dataclasses.field(default_factory=set)
    """Skills whose full docs have been loaded this turn (for auto-inject dedup)."""

    notified: bool = False
    """Set to True when the agent sends a notification via ``marcel(action="notify")``.

    Used by the job executor to skip its own automatic notification when the
    agent already delivered a message to the user during the run.
    """


def build_server_context(cwd: str | None = None) -> str:
    """Build a server/environment context block for admin users.

    Describes the server environment: hostname, home directory, working
    directory, and available tools.

    Args:
        cwd: The effective working directory (shown in the context block).

    Returns:
        Markdown string describing the server environment.
    """
    home = str(Path.home())
    lines = ['## Server Context (Admin)']

    try:
        hostname = Path('/etc/hostname').read_text(encoding='utf-8').strip()
        lines.append(f'**Host:** `{hostname}`')
    except OSError:
        pass

    lines.append(f'**Home directory:** `{home}`')

    # Docker socket — available when managing sibling containers
    if Path('/var/run/docker.sock').exists():
        lines.append('**Docker:** socket available — use `docker` CLI to list, inspect, restart, exec into containers')

    effective_cwd = cwd or home
    lines.append(f'**Working directory:** `{effective_cwd}`')

    lines += [
        '',
        'You have full CLI capabilities: `bash`, file I/O, `git_*`, and `claude_code` delegation.',
        f'When the user refers to "home folder", "server files", or "the NUC", '
        f'they mean this machine — start from `{home}`.',
    ]

    return '\n'.join(lines)


def load_channel_prompt(channel: str) -> str:
    """Load channel-specific prompt from the data root, falling back to defaults.

    Looks for ``<data_root>/channels/<channel>.md`` first (user-editable),
    then falls back to the bundled default at
    ``src/marcel_core/defaults/channels/<channel>.md``.

    Args:
        channel: The channel name (e.g., 'telegram', 'cli').

    Returns:
        The channel prompt body text (frontmatter stripped).
    """
    from marcel_core.skills.loader import _parse_frontmatter

    # 1. User-editable override in data root
    try:
        from marcel_core.config import settings

        data_channel = settings.data_dir / 'channels' / f'{channel}.md'
        if data_channel.exists():
            _, body = _parse_frontmatter(data_channel.read_text(encoding='utf-8'))
            return body
    except Exception:
        log.debug('Could not check data root channel prompt for %s', channel, exc_info=True)

    # 2. Bundled default
    default = _DEFAULTS_CHANNELS / f'{channel}.md'
    if default.exists():
        _, body = _parse_frontmatter(default.read_text(encoding='utf-8'))
        return body

    # 3. Generic fallback
    return f'You are responding via the {channel} channel.'


async def build_instructions_async(deps: MarcelDeps, query: str = '') -> str:
    """Build dynamic system instructions with AI-selected memories.

    Assembles the full system prompt from:
    1. MARCEL.md files (global + per-user instructions)
    2. User profile
    3. Server context (admin only)
    4. Skill documentation
    5. AI-selected memories
    6. Channel format hints

    Args:
        deps: The MarcelDeps context.
        query: The user's query (for memory selection).

    Returns:
        Complete system prompt string.
    """
    from marcel_core.agent.marcelmd import format_marcelmd_for_prompt, load_marcelmd_files
    from marcel_core.memory.selector import select_relevant_memories
    from marcel_core.skills.loader import format_skill_index, load_skills
    from marcel_core.storage import load_user_profile

    profile = load_user_profile(deps.user_slug)

    # Load MARCEL.md instructions
    marcelmd = format_marcelmd_for_prompt(load_marcelmd_files(deps.user_slug))

    # Load skill index (compact — full docs loaded on demand via marcel tool)
    skills = format_skill_index(load_skills(deps.user_slug))

    # Select relevant memories if we have a query
    memory_content = ''
    if query:
        try:
            selected_memories = await select_relevant_memories(deps.user_slug, query)
            if selected_memories:
                memory_parts = [content for _, content in selected_memories]
                memory_content = '\n\n---\n\n'.join(memory_parts)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning('Memory selection failed: %s', exc)

    channel_prompt = load_channel_prompt(deps.channel)

    lines: list[str] = []

    # MARCEL.md instructions (identity, role, tone)
    if marcelmd:
        lines += [marcelmd, '']

    # User profile
    lines += [
        f'## What you know about {deps.user_slug}',
        profile or '(no profile information yet)',
        '',
    ]

    if deps.role == 'admin':
        lines += [build_server_context(deps.cwd), '']

    # Skill index (compact — use marcel(action="read_skill") for full docs)
    if skills:
        lines += [
            '## Skills',
            'Before calling an integration for the first time, '
            'use `marcel(action="read_skill", name="...")` to load its full documentation.',
            '',
            skills,
            '',
        ]

    if memory_content:
        lines += ['## Memory', memory_content, '']

    # Channel-specific delivery guidance
    lines += ['## Channel', channel_prompt]

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

    channel_prompt = load_channel_prompt(deps.channel)

    lines = [
        f'You are Marcel, a warm and capable personal assistant for {deps.user_slug}.',
        '',
        f'## What you know about {deps.user_slug}',
        profile or '(no profile information yet)',
        '',
    ]

    if deps.role == 'admin':
        lines += [build_server_context(deps.cwd), '']

    # Channel-specific delivery guidance
    lines += ['## Channel', channel_prompt]

    return '\n'.join(lines)
