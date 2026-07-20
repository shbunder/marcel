"""Channel guidance as a pydantic-ai capability (FEAT-260720-089958).

Lives in the ``channels`` domain package per the convention decided in
ADR-260720-f23f23: a domain package owns its capability factory
(``skills/capability.py``, ``connectors/toolset.py``, this module);
``capabilities/`` hosts run-shaping packages with no other domain home.
The bundled per-channel prompt files live beside it in
``channels/prompts/``.

The channel's agent-side influence — the ``# <Channel> — how to respond``
guidance block and, for rich-UI channels, the A2UI component catalog — rides
as one eager :class:`~pydantic_ai.capabilities.Capability` attached by the
composition root, instead of being hand-concatenated into the system prompt
string. Same migration the Memory notebook block made (ADR-260718-0cf8e8:
capabilities meet only in ``composition.py``); the prompt *text* is
unchanged, only the contributing mechanism moved.

The transport half of the channel world (``channels/adapter.py``: the
``send_*`` protocol, ``dispatch_event``, :class:`ChannelCapabilities`) is
I/O, not agent composition, and is deliberately untouched — a channel
plugin's ``rich_ui`` flag is still what gates the catalog, resolved through
:func:`~marcel_core.channels.adapter.channel_supports_rich_ui`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.capabilities import Capability

    from marcel_core.harness.context import MarcelDeps

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_CHANNEL_PROMPTS_DIR = Path(__file__).resolve().parent / 'prompts'


def load_channel_prompt(channel: str) -> str:
    """Load channel-specific prompt from the data root, falling back to the kernel bundle.

    Looks for ``<data_root>/channels/<channel>.md`` first (user-editable),
    then falls back to the bundled kernel prompt at
    ``src/marcel_core/channels/prompts/<channel>.md``.

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

    # 2. Kernel-bundled prompt for kernel channel types (cli, ws, ios, app, job)
    bundled = _CHANNEL_PROMPTS_DIR / f'{channel}.md'
    if bundled.exists():
        _, body = _parse_frontmatter(bundled.read_text(encoding='utf-8'))
        return _strip_channel_preamble(body).strip()

    # 3. Generic fallback — keep as plain guidance (no preamble to strip)
    return f'Respond in a format appropriate for the {channel} channel.'


_A2UI_PREAMBLE = (
    'Prefer these structured components over plain-text summaries when the data '
    'fits one of them. Emit via `marcel(action="render", component="...", props={...})` — '
    'do NOT write the component JSON directly in your reply. On Telegram the user gets a '
    '"View in app" button that opens the Mini App and renders the component natively.'
)


def build_channel_capability(
    channel: str,
    user_slug: str | None = None,
    role: str = 'user',
) -> 'Capability[MarcelDeps]':
    """The channel-guidance capability for one turn.

    Always present when a channel is named — the header block alone still
    anchors channel identity even for prompt-less channels. Instructions
    are assembled once per build from static inputs (channel prompt file,
    component registry), so the block is cache-stable across a
    conversation on the same channel.
    """
    from pydantic_ai.capabilities import Capability

    from marcel_core.channels.adapter import channel_supports_rich_ui

    blocks: list[str] = []

    if channel_supports_rich_ui(channel) and user_slug is not None:
        from marcel_core.skills.loader import format_components_catalog, load_skills

        catalog = format_components_catalog(load_skills(user_slug, role))
        if catalog:
            blocks.append(
                '\n'.join(['# A2UI Components — how to show rich content', '', _A2UI_PREAMBLE, '', catalog]).rstrip()
            )

    channel_prompt = load_channel_prompt(channel)
    channel_block = [f'# {channel.capitalize()} — how to respond']
    if channel_prompt:
        channel_block += ['', channel_prompt]
    blocks.append('\n'.join(channel_block).rstrip())

    return Capability(
        id=f'channel:{channel}',
        description=f'How to respond on the {channel} channel.',
        instructions='\n\n'.join(blocks),
        defer_loading=False,
    )
