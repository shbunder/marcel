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
class TurnState:
    """Mutable state accumulated during a single agent turn.

    Kept separate from ``MarcelDeps`` so the dependency container stays
    immutable identity/config and all per-turn state lives in one place.
    Tools mutate fields on ``ctx.deps.turn`` (e.g. ``turn.read_skills``,
    ``turn.notified``); post-run code (like the job executor) reads them
    to decide what to do next.
    """

    read_skills: set[str] = dataclasses.field(default_factory=set)
    """Skills whose full docs have been loaded this turn (for auto-inject dedup)."""

    web_search_count: int = 0
    """Count of ``web(action="search")`` calls made so far this turn.

    Enforced against ``MAX_SEARCHES_PER_TURN`` in the web dispatcher so a
    runaway loop cannot burn the configured search backend's quota. Resets
    every turn because ``TurnState`` is constructed fresh per turn.
    """

    notified: bool = False
    """Set to True when the agent sends a notification via ``marcel(action="notify")``.

    Used by the job executor to skip its own automatic notification when the
    agent already delivered a message to the user during the run.
    """

    suppress_notify: bool = False
    """When True, ``marcel(action="notify")`` is a no-op.

    Set by the job executor when the job's notify policy forbids
    agent-initiated delivery (``silent`` or ``on_failure``). Turns
    ``marcel(action="notify")`` into a suppression notice so the policy
    is the single source of truth for whether a job can reach the user.
    """


@pydantic_dc.dataclass
class MarcelDeps:
    """Dependencies injected into Marcel agent tools via RunContext.

    This is the deps_type for pydantic-ai Agent. Tools receive RunContext[MarcelDeps]
    which provides access to user context, conversation state, and channel information.

    Uses ``pydantic.dataclasses.dataclass`` for validation while keeping the
    dataclass-style API that pydantic-ai expects for ``deps_type``.

    Fields are either immutable identity/config (``user_slug``, ``channel``,
    ``model``, ``role``, ``cwd``) or the per-turn state holder ``turn``.
    Do not add stateful flags directly here — add them to :class:`TurnState`.
    """

    user_slug: str
    """The user's slug identifier (directory name under ~/.marcel/users/)."""

    conversation_id: str
    """The active conversation identifier."""

    channel: str
    """The originating channel: 'cli', 'telegram', 'app', 'ios', 'websocket'."""

    model: str | None = None
    """Optional model override — fully-qualified pydantic-ai string
    (e.g. ``'anthropic:claude-opus-4-6'``, ``'openai:gpt-4o'``)."""

    role: str = 'user'
    """The user's role: 'admin' or 'user'."""

    cwd: str | None = None
    """Working directory for bash and file operations.

    For admin CLI sessions this is the directory where the CLI was invoked.
    For admin non-CLI sessions this defaults to the user's home directory.
    None falls back to the project root.
    """

    turn: TurnState = dataclasses.field(default_factory=TurnState)
    """Per-turn mutable state (tools mutate this, never the parent deps)."""


def _strip_leading_h1_safe(body: str) -> str:
    """Strip a leading ``# Heading`` line from *body* for the prompt builder.

    Thin wrapper around :func:`marcel_core.harness.marcelmd._strip_leading_h1`
    so both ``profile.md`` (with its own ``# Shaun`` H1) and already-cleaned
    ``MARCEL.md`` content can be re-stripped defensively before being pasted
    under a prompt-builder-chosen H1 wrapper.
    """
    from marcel_core.harness.marcelmd import _strip_leading_h1

    return _strip_leading_h1(body).strip()


def build_server_context(cwd: str | None = None) -> str:
    """Build a server/environment context block for admin users.

    Describes the server environment: hostname, home directory, working
    directory, and available tools. Emitted as an H2 sub-block under the
    ``# Shaun`` (or whichever user) H1 in the assembled system prompt.

    Args:
        cwd: The effective working directory (shown in the context block).

    Returns:
        Markdown string describing the server environment.
    """
    home = str(Path.home())
    lines = ['## Server context']

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
    from marcel_core.harness.marcelmd import _strip_channel_preamble
    from marcel_core.skills.loader import _parse_frontmatter

    # 1. User-editable override in data root
    try:
        from marcel_core.config import settings

        data_channel = settings.data_dir / 'channels' / f'{channel}.md'
        if data_channel.exists():
            _, body = _parse_frontmatter(data_channel.read_text(encoding='utf-8'))
            return _strip_channel_preamble(body).strip()
    except Exception:
        log.debug('Could not check data root channel prompt for %s', channel, exc_info=True)

    # 2. Bundled default
    default = _DEFAULTS_CHANNELS / f'{channel}.md'
    if default.exists():
        _, body = _parse_frontmatter(default.read_text(encoding='utf-8'))
        return _strip_channel_preamble(body).strip()

    # 3. Generic fallback — keep as plain guidance (no preamble to strip)
    return f'Respond in a format appropriate for the {channel} channel.'


async def build_instructions_async(deps: MarcelDeps, query: str = '') -> str:
    """Build the system prompt as five clean H1 blocks.

    Structure:
        # Marcel — who you are       (global MARCEL.md, H1 + self-ref blockquote stripped)
        # <user> — who the user is   (profile body, with server-context H2 folded in for admin)
        # Skills — what you can do   (compact index + on-demand read hint)
        # Memory — what you know     (compact index + search/read hint)
        # <channel> — how to respond (channel guidance, preamble stripped)

    The ``query`` argument is kept for API compatibility but is no longer
    used — memory is now loaded on demand via ``search_memory`` / ``read_memory``
    instead of being pre-selected each turn. See ISSUE-068.
    """
    from marcel_core.channels.adapter import channel_supports_rich_ui
    from marcel_core.harness.marcelmd import format_marcelmd_for_prompt, load_marcelmd_files
    from marcel_core.skills.loader import format_components_catalog, format_skill_index, load_skills
    from marcel_core.storage import load_user_profile
    from marcel_core.storage.memory import format_memory_index, scan_memory_headers

    # -- Load everything up front (cheap file reads) ------------------------
    marcelmd = format_marcelmd_for_prompt(load_marcelmd_files(deps.user_slug))
    profile = load_user_profile(deps.user_slug).strip()

    loaded_skills = load_skills(deps.user_slug)
    skill_index = format_skill_index(loaded_skills)
    components_catalog = format_components_catalog(loaded_skills) if channel_supports_rich_ui(deps.channel) else ''

    memory_headers = scan_memory_headers(deps.user_slug)
    memory_index = format_memory_index(memory_headers)

    channel_prompt = load_channel_prompt(deps.channel)
    channel_label = deps.channel.capitalize()

    # -- Assemble five H1 blocks --------------------------------------------
    blocks: list[str] = []

    # Block 1: who Marcel is
    marcel_block = ['# Marcel — who you are']
    if marcelmd:
        marcel_block += ['', _strip_leading_h1_safe(marcelmd)]
    else:
        marcel_block += ['', f'You are Marcel, a warm and capable personal assistant for {deps.user_slug}.']
    blocks.append('\n'.join(marcel_block).rstrip())

    # Block 2: who the user is (+ server context for admin)
    user_block = [f'# {deps.user_slug.capitalize()} — who the user is']
    if profile:
        user_block += ['', _strip_leading_h1_safe(profile)]
    else:
        user_block += ['', '(no profile information yet)']
    if deps.role == 'admin':
        user_block += ['', build_server_context(deps.cwd)]
    blocks.append('\n'.join(user_block).rstrip())

    # Block 3: what you can do
    skill_block = ['# Skills — what you can do']
    if skill_index:
        skill_block += [
            '',
            skill_index,
            '',
            '*Full docs are loaded on demand — call `marcel(action="read_skill", name="...")` '
            'before using an integration for the first time.*',
        ]
    else:
        skill_block += ['', '(no skills configured)']
    if components_catalog:
        skill_block += [
            '',
            '## A2UI Components',
            '',
            'Prefer these structured components over plain-text summaries when the data '
            'fits one of them. Emit via `marcel(action="render", component="...", props={...})` — '
            'do NOT write the component JSON directly in your reply. On Telegram the user gets a '
            '"View in app" button that opens the Mini App and renders the component natively.',
            '',
            components_catalog,
        ]
    blocks.append('\n'.join(skill_block).rstrip())

    # Block 4: what you should know (compact memory index — no dumps)
    memory_block = ['# Memory — what you should know']
    if memory_index:
        memory_block += [
            '',
            memory_index,
            '',
            '*Search with `marcel(action="search_memory", query="...")` or load a specific '
            'file with `marcel(action="read_memory", name="...")`.*',
        ]
    else:
        memory_block += ['', '(no memories saved yet)']
    blocks.append('\n'.join(memory_block).rstrip())

    # Block 5: how to respond (channel guidance)
    channel_block = [f'# {channel_label} — how to respond']
    if channel_prompt:
        channel_block += ['', channel_prompt]
    blocks.append('\n'.join(channel_block).rstrip())

    return '\n\n'.join(blocks)


def build_instructions(deps: MarcelDeps) -> str:
    """Sync fallback for the five-block system prompt.

    Used during Agent initialization when the async builder is not
    available. Produces the same H1 structure as
    :func:`build_instructions_async` — the two must not diverge.
    """
    from marcel_core.harness.marcelmd import format_marcelmd_for_prompt, load_marcelmd_files
    from marcel_core.skills.loader import format_skill_index, load_skills
    from marcel_core.storage import load_user_profile
    from marcel_core.storage.memory import format_memory_index, scan_memory_headers

    marcelmd = format_marcelmd_for_prompt(load_marcelmd_files(deps.user_slug))
    profile = load_user_profile(deps.user_slug).strip()
    skill_index = format_skill_index(load_skills(deps.user_slug))
    memory_index = format_memory_index(scan_memory_headers(deps.user_slug))
    channel_prompt = load_channel_prompt(deps.channel)
    channel_label = deps.channel.capitalize()

    blocks: list[str] = []

    marcel_block = ['# Marcel — who you are']
    if marcelmd:
        marcel_block += ['', _strip_leading_h1_safe(marcelmd)]
    else:
        marcel_block += ['', f'You are Marcel, a warm and capable personal assistant for {deps.user_slug}.']
    blocks.append('\n'.join(marcel_block).rstrip())

    user_block = [f'# {deps.user_slug.capitalize()} — who the user is']
    if profile:
        user_block += ['', _strip_leading_h1_safe(profile)]
    else:
        user_block += ['', '(no profile information yet)']
    if deps.role == 'admin':
        user_block += ['', build_server_context(deps.cwd)]
    blocks.append('\n'.join(user_block).rstrip())

    skill_block = ['# Skills — what you can do']
    if skill_index:
        skill_block += [
            '',
            skill_index,
            '',
            '*Full docs are loaded on demand — call `marcel(action="read_skill", name="...")` '
            'before using an integration for the first time.*',
        ]
    else:
        skill_block += ['', '(no skills configured)']
    blocks.append('\n'.join(skill_block).rstrip())

    memory_block = ['# Memory — what you should know']
    if memory_index:
        memory_block += [
            '',
            memory_index,
            '',
            '*Search with `marcel(action="search_memory", query="...")` or load a specific '
            'file with `marcel(action="read_memory", name="...")`.*',
        ]
    else:
        memory_block += ['', '(no memories saved yet)']
    blocks.append('\n'.join(memory_block).rstrip())

    channel_block = [f'# {channel_label} — how to respond']
    if channel_prompt:
        channel_block += ['', channel_prompt]
    blocks.append('\n'.join(channel_block).rstrip())

    return '\n\n'.join(blocks)
