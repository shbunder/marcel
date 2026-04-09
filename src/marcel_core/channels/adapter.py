"""Channel adapter protocol and base classes.

Channels are thin clients that handle transport and formatting.
The harness knows channel capabilities and formats responses accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        """Send incremental text from assistant.

        Args:
            text: Text delta to send.
        """
        ...

    async def send_tool_call_started(self, tool_call_id: str, tool_name: str) -> None:
        """Notify that a tool invocation started.

        Args:
            tool_call_id: Unique identifier for this tool call.
            tool_name: Name of the tool being called.
        """
        ...

    async def send_tool_call_completed(self, tool_call_id: str, tool_name: str, result: str, is_error: bool) -> None:
        """Notify that a tool invocation completed.

        Args:
            tool_call_id: Unique identifier for this tool call.
            tool_name: Name of the tool that was called.
            result: Tool execution result.
            is_error: Whether the tool call resulted in an error.
        """
        ...

    async def send_run_finished(self, cost_usd: float | None, is_error: bool) -> None:
        """Notify that the turn execution finished.

        Args:
            cost_usd: Total cost for this turn (if available).
            is_error: Whether the turn resulted in an error.
        """
        ...

    def format_text(self, text: str) -> str:
        """Format text for this channel.

        Channels can override this to apply channel-specific formatting
        (e.g., HTML escaping, markdown → HTML conversion).

        Args:
            text: Raw text to format.

        Returns:
            Formatted text ready for transmission.
        """
        return text
