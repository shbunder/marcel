"""Channel adapter protocol and base classes.

Channels are thin clients that handle transport and formatting.
The harness knows channel capabilities and formats responses accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marcel_core.harness.runner import (
    MarcelEvent,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
)


@dataclass
class ChannelCapabilities:
    """Declares what a channel supports."""

    markdown: bool = True
    """Supports markdown formatting."""

    rich_ui: bool = False
    """Supports cards, buttons, structured data."""

    streaming: bool = True
    """Real-time token streaming."""

    progress_updates: bool = True
    """Can show intermediate progress (via notify tool)."""

    attachments: bool = False
    """Can receive/send files."""

    interaction_profile: str = 'continuous'
    """How the channel's conversation is sealed (FEAT-260707-89a886).

    ``continuous`` (default — every existing channel) keeps one rolling
    conversation that seals on idle/budget only (Telegram threads spanning
    months). ``session`` channels have an explicit end — client disconnect
    or idle — at which the segment is sealed + summarized once, so the next
    session starts from the rolling summary instead of replaying the whole
    prior transcript (ADR-260707-ac8f49).
    """

    def __post_init__(self) -> None:
        if self.interaction_profile not in ('continuous', 'session'):
            raise ValueError(f"interaction_profile must be 'continuous' or 'session', not {self.interaction_profile!r}")


# Channels whose interaction is inherently session-scoped without a registered
# plugin — a CLI invocation ends, a WebSocket connection closes. Native app
# clients (app/ios/macos) and messaging (telegram) stay continuous.
_BUILTIN_SESSION_CHANNELS = frozenset({'cli', 'websocket'})


# Built-in rich-UI channels the kernel knows about without a registered
# plugin — native clients (app, ios, macos) consume the /api/components
# catalog but have no Python module to register themselves, and `websocket`
# is a transport primitive that lives in this repo. Transport plugins
# (telegram, and future signal/discord channel habitats) declare
# `rich_ui` on their `ChannelCapabilities` at registration time and are
# resolved via the plugin registry below — they do not appear here.
_BUILTIN_RICH_UI_CHANNELS = frozenset({'websocket', 'app', 'ios', 'macos'})


def channel_supports_rich_ui(channel: str) -> bool:
    """Return True if the channel can render A2UI component artifacts.

    Resolution order:

    1. Registered :class:`~marcel_core.plugin.channels.ChannelPlugin` — the
       plugin's ``capabilities.rich_ui`` flag wins if the channel is
       registered.
    2. Built-in rich-UI set — kernel-native clients (websocket, app, ios,
       macos) that have no plugin module.
    3. Otherwise ``False``.

    Used by the channel capability (``channels/capability.py``) to decide
    whether its instructions carry the A2UI component catalog. Text-only
    channels (cli, job) should not be told about components they cannot
    render.
    """
    from marcel_core.plugin.channels import channel_has_rich_ui

    registered = channel_has_rich_ui(channel)
    if registered is not None:
        return registered
    return channel in _BUILTIN_RICH_UI_CHANNELS


def channel_interaction_profile(channel: str) -> str:
    """The channel's interaction profile — ``continuous`` or ``session``.

    Resolution mirrors :func:`channel_supports_rich_ui`: a registered
    :class:`~marcel_core.plugin.channels.ChannelPlugin`'s declared profile
    wins; otherwise the built-in session set (cli, websocket); otherwise
    ``continuous`` (FEAT-260707-89a886). ``session`` channels seal +
    summarize at session end (see :func:`seal_session_if_needed`).
    """
    from marcel_core.plugin.channels import channel_registered_interaction_profile

    registered = channel_registered_interaction_profile(channel)
    if registered is not None:
        return registered
    return 'session' if channel in _BUILTIN_SESSION_CHANNELS else 'continuous'


async def seal_session_if_needed(user_slug: str, channel: str) -> bool:
    """Seal + summarize at a session boundary, for ``session`` channels only.

    Called at an observable session end (a WebSocket disconnect). A
    ``continuous`` channel keeps rolling — its sealing stays idle/budget
    driven — so this is a no-op for it. Returns True if a seal happened.
    """
    if channel_interaction_profile(channel) != 'session':
        return False
    from marcel_core.storage.conversation import has_active_content

    if not has_active_content(user_slug, channel):
        return False
    from marcel_core.memory.summarizer import summarize_active_segment

    return await summarize_active_segment(user_slug, channel, trigger='session_end')


class ChannelAdapter(Protocol):
    """Protocol for Marcel channel adapters.

    Channels implement this protocol to receive events from the harness
    and format them appropriately for their transport layer.
    """

    @property
    def capabilities(self) -> ChannelCapabilities:
        """Return the capabilities of this channel."""
        ...

    async def send_text_delta(self, text: str) -> None:
        """Send incremental text from assistant."""
        ...

    async def send_text_message_start(self) -> None:
        """Notify that a text message block started."""
        ...

    async def send_text_message_end(self) -> None:
        """Notify that a text message block ended."""
        ...

    async def send_tool_call_started(self, tool_call_id: str, tool_name: str) -> None:
        """Notify that a tool invocation started."""
        ...

    async def send_tool_call_completed(self, tool_call_id: str, tool_name: str, result: str, is_error: bool) -> None:
        """Notify that a tool invocation completed."""
        ...

    async def send_run_finished(self, cost_usd: float | None, is_error: bool) -> None:
        """Notify that the turn execution finished."""
        ...

    async def send_error(self, message: str) -> None:
        """Send an error message to the client."""
        ...

    def format_text(self, text: str) -> str:
        """Format text for this channel."""
        return text


async def dispatch_event(
    adapter: ChannelAdapter,
    event: MarcelEvent,
    *,
    text_started: bool,
) -> bool:
    """Route a MarcelEvent to the appropriate adapter method.

    This eliminates the isinstance dispatch chains in channel endpoints.

    Args:
        adapter: The channel adapter to send events to.
        event: The event from the harness runner.
        text_started: Whether a text message block is currently open.

    Returns:
        Updated ``text_started`` flag (True if a text block is open).
    """
    if isinstance(event, RunStarted):
        pass  # Lifecycle event — no adapter action needed

    elif isinstance(event, TextDelta):
        if not text_started:
            await adapter.send_text_message_start()
            text_started = True
        await adapter.send_text_delta(event.text)

    elif isinstance(event, ToolCallStarted):
        if text_started:
            await adapter.send_text_message_end()
            text_started = False
        await adapter.send_tool_call_started(event.tool_call_id, event.tool_name)

    elif isinstance(event, ToolCallCompleted):
        await adapter.send_tool_call_completed(event.tool_call_id, event.tool_name, event.result, event.is_error)

    elif isinstance(event, RunFinished):
        if text_started:
            await adapter.send_text_message_end()
            text_started = False
        await adapter.send_run_finished(event.total_cost_usd, event.is_error)

    return text_started
