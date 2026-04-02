"""AG-UI-aligned event types for the Marcel agent streaming protocol.

These events are yielded by ``stream_response()`` and consumed by transport
layers (WebSocket, Telegram, etc.).  They follow the AG-UI event taxonomy
but are implemented as plain dataclasses with no external SDK dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunStarted:
    """Emitted once at the beginning of an agent turn."""

    type: Literal['run_started'] = field(default='run_started', init=False)
    thread_id: str = ''

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {'type': self.type}
        if self.thread_id:
            d['thread_id'] = self.thread_id
        return d


@dataclass(frozen=True)
class RunFinished:
    """Emitted once after a complete turn with cost/usage metadata."""

    type: Literal['run_finished'] = field(default='run_finished', init=False)
    total_cost_usd: float | None = None
    num_turns: int = 0
    session_id: str | None = None
    is_error: bool = False

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {'type': self.type}
        if self.total_cost_usd is not None:
            d['cost_usd'] = self.total_cost_usd
        if self.num_turns:
            d['turns'] = self.num_turns
        return d


@dataclass(frozen=True)
class RunError:
    """Emitted when the agent turn fails unrecoverably."""

    type: Literal['run_error'] = field(default='run_error', init=False)
    message: str = ''

    def to_dict(self) -> dict[str, object]:
        return {'type': self.type, 'message': self.message}


# ---------------------------------------------------------------------------
# Text message events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextMessageStart:
    """Marks the beginning of a text message from the assistant."""

    type: Literal['text_message_start'] = field(default='text_message_start', init=False)
    message_id: str = ''

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {'type': self.type}
        if self.message_id:
            d['message_id'] = self.message_id
        return d


@dataclass(frozen=True)
class TextMessageContent:
    """A chunk of streaming text content."""

    type: Literal['text_message_content'] = field(default='text_message_content', init=False)
    text: str = ''

    def to_dict(self) -> dict[str, object]:
        return {'type': self.type, 'text': self.text}


@dataclass(frozen=True)
class TextMessageEnd:
    """Marks the end of a text message from the assistant."""

    type: Literal['text_message_end'] = field(default='text_message_end', init=False)

    def to_dict(self) -> dict[str, object]:
        return {'type': self.type}


# ---------------------------------------------------------------------------
# Tool call events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallStart:
    """Emitted when a tool invocation begins."""

    type: Literal['tool_call_start'] = field(default='tool_call_start', init=False)
    tool_call_id: str = ''
    tool_name: str = ''

    def to_dict(self) -> dict[str, object]:
        return {
            'type': self.type,
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
        }


@dataclass(frozen=True)
class ToolCallEnd:
    """Emitted when a tool invocation completes."""

    type: Literal['tool_call_end'] = field(default='tool_call_end', init=False)
    tool_call_id: str = ''

    def to_dict(self) -> dict[str, object]:
        return {'type': self.type, 'tool_call_id': self.tool_call_id}


@dataclass(frozen=True)
class ToolCallResult:
    """Emitted with the result of a tool invocation."""

    type: Literal['tool_call_result'] = field(default='tool_call_result', init=False)
    tool_call_id: str = ''
    is_error: bool = False
    summary: str = ''

    def to_dict(self) -> dict[str, object]:
        return {
            'type': self.type,
            'tool_call_id': self.tool_call_id,
            'is_error': self.is_error,
            'summary': self.summary,
        }


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

AgentEvent = (
    RunStarted
    | RunFinished
    | RunError
    | TextMessageStart
    | TextMessageContent
    | TextMessageEnd
    | ToolCallStart
    | ToolCallEnd
    | ToolCallResult
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, *, max_len: int = 200) -> str:
    """Truncate text for tool result summaries."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + '...'
