"""WebSocket channel adapter for Marcel v2 harness."""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket

from marcel_core.channels.adapter import ChannelCapabilities

log = logging.getLogger(__name__)


class WebSocketAdapter:
    """WebSocket channel adapter.

    Implements the ChannelAdapter protocol for WebSocket connections.
    Sends AG-UI compatible events to the client.
    """

    def __init__(self, websocket: WebSocket) -> None:
        """Initialize the adapter.

        Args:
            websocket: The FastAPI WebSocket connection.
        """
        self.websocket = websocket
        self._capabilities = ChannelCapabilities(
            markdown=True,
            rich_ui=True,  # Can render structured data
            streaming=True,
            progress_updates=True,
            attachments=False,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        """Return WebSocket capabilities."""
        return self._capabilities

    async def send_text_delta(self, text: str) -> None:
        """Send incremental text token.

        Args:
            text: Text delta to stream.
        """
        await self.websocket.send_text(json.dumps({'type': 'token', 'text': text}))

    async def send_tool_call_started(self, tool_call_id: str, tool_name: str) -> None:
        """Send tool call start event.

        Args:
            tool_call_id: Unique tool call identifier.
            tool_name: Name of the tool being invoked.
        """
        await self.websocket.send_text(
            json.dumps(
                {
                    'type': 'tool_call_start',
                    'tool_call_id': tool_call_id,
                    'tool_name': tool_name,
                }
            )
        )

    async def send_tool_call_completed(self, tool_call_id: str, tool_name: str, result: str, is_error: bool) -> None:
        """Send tool call completion event.

        Args:
            tool_call_id: Unique tool call identifier.
            tool_name: Name of the tool.
            result: Tool execution result (truncated summary).
            is_error: Whether the tool call failed.
        """
        # Send end event
        await self.websocket.send_text(json.dumps({'type': 'tool_call_end', 'tool_call_id': tool_call_id}))

        # Send result event
        await self.websocket.send_text(
            json.dumps(
                {
                    'type': 'tool_call_result',
                    'tool_call_id': tool_call_id,
                    'tool_name': tool_name,
                    'summary': result[:500],  # Truncate for display
                    'is_error': is_error,
                }
            )
        )

    async def send_run_finished(self, cost_usd: float | None, is_error: bool) -> None:
        """Send turn completion event.

        Args:
            cost_usd: Total cost for this turn (if available).
            is_error: Whether the turn failed.
        """
        msg: dict[str, object] = {'type': 'done'}
        if cost_usd is not None:
            msg['cost_usd'] = cost_usd
        if is_error:
            msg['is_error'] = True
        await self.websocket.send_text(json.dumps(msg))

    async def send_error(self, message: str) -> None:
        """Send an error message to the client.

        Args:
            message: Error message to display.
        """
        await self.websocket.send_text(json.dumps({'type': 'error', 'message': message}))

    async def send_conversation_started(self, conversation_id: str) -> None:
        """Notify that a new conversation was created.

        Args:
            conversation_id: The new conversation identifier.
        """
        await self.websocket.send_text(json.dumps({'type': 'started', 'conversation': conversation_id}))

    async def send_text_message_start(self) -> None:
        """Notify that a text message block started."""
        await self.websocket.send_text(json.dumps({'type': 'text_message_start'}))

    async def send_text_message_end(self) -> None:
        """Notify that a text message block ended."""
        await self.websocket.send_text(json.dumps({'type': 'text_message_end'}))

    def format_text(self, text: str) -> str:
        """Format text for WebSocket (no transformation needed)."""
        return text
