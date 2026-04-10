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
