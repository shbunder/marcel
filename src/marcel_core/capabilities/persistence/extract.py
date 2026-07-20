"""Converters between Marcel's HistoryMessage JSONL and pydantic-ai messages.

Read side: :func:`messages_to_model` turns stored ``HistoryMessage`` lines
into provider-shaped ``ModelMessage`` lists, served as-is — in-run shaping
belongs to the compaction stack.

Write side: :func:`extract_tool_history` walks a run's **new** messages and
produces the tool-related ``HistoryMessage`` entries to persist (assistant
tool calls, tool returns, retry prompts) exactly as the model saw them —
oversized returns were already reduced at return time by
OverflowingToolOutput. User prompts and final text responses are
deliberately excluded — the runner owns those (error tails and
explain-tier text never exist in agent messages).

Moved verbatim from ``harness/runner.py`` (FEAT-260718-ed6d63) so the
persistence capability and the runner's channel-event emission share one
implementation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from marcel_core.storage.history import HistoryMessage, ToolCall


def messages_to_model(
    messages: list[HistoryMessage],
) -> list[ModelMessage]:
    """Convert internal HistoryMessage objects to pydantic-ai ModelMessage format.

    Handles user, assistant (with tool calls), and tool result messages.
    Serves stored content as-is: in-run shaping (blanking old tool results,
    clamping runaway parts) is the compaction capabilities' job now
    (STORY-260718-406de0) — disk keeps full fidelity, each request pays
    only for what the compaction stack lets through.
    """
    result: list[ModelMessage] = []
    # Collect consecutive tool-result messages into a single ModelRequest
    pending_tool_returns: list[ToolReturnPart] = []

    def _flush_tool_returns() -> None:
        if pending_tool_returns:
            result.append(ModelRequest(parts=list(pending_tool_returns)))
            pending_tool_returns.clear()

    for msg in messages:
        if msg.role == 'user':
            _flush_tool_returns()
            if not msg.text:
                continue
            result.append(ModelRequest(parts=[UserPromptPart(content=msg.text, timestamp=msg.timestamp)]))

        elif msg.role == 'assistant':
            _flush_tool_returns()
            parts: list[TextPart | ToolCallPart] = []
            if msg.text:
                parts.append(TextPart(content=msg.text))
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(
                        ToolCallPart(
                            tool_name=tc.name,
                            args=tc.arguments,
                            tool_call_id=tc.id,
                        )
                    )
            if parts:
                result.append(ModelResponse(parts=parts, timestamp=msg.timestamp))

        elif msg.role == 'tool':
            content = msg.text or f'({msg.tool_name or "tool"} completed with no output)'
            pending_tool_returns.append(
                ToolReturnPart(
                    tool_name=msg.tool_name or 'unknown',
                    content=content,
                    tool_call_id=msg.tool_call_id or '',
                    outcome='failed' if msg.is_error else 'success',
                    timestamp=msg.timestamp,
                )
            )

        elif msg.role == 'system':
            _flush_tool_returns()
            if msg.text:
                result.append(ModelRequest(parts=[UserPromptPart(content=msg.text, timestamp=msg.timestamp)]))

    _flush_tool_returns()
    return result


def extract_tool_history(
    new_messages: list[ModelMessage],
    user_slug: str,
    conversation_id: str,
) -> list[HistoryMessage]:
    """Extract tool call and result history from a run's new messages.

    Walks the message list and converts tool-related parts into
    HistoryMessage entries for JSONL storage.

    Returns assistant messages with tool_calls and tool-role result
    messages. Skips user prompts and text-only responses (the runner owns
    those). Feed it a run *delta* (``new_messages()`` / a snapshot suffix),
    never the full history — re-walking history re-appends it
    (STORY-260718-bfb1ac).
    """
    entries: list[HistoryMessage] = []
    now = datetime.now(tz=timezone.utc)

    for msg in new_messages:
        if isinstance(msg, ModelResponse):
            tool_calls = msg.tool_calls
            if not tool_calls:
                continue
            # Build HistoryMessage for assistant with tool calls
            tc_list = [
                ToolCall(
                    id=tc.tool_call_id,
                    name=tc.tool_name,
                    arguments=tc.args_as_dict()
                    if callable(getattr(tc, 'args_as_dict', None))
                    else (tc.args if isinstance(tc.args, dict) else {}),
                )
                for tc in tool_calls
            ]
            # Collect any text parts in this response
            text_parts = [p.content for p in msg.parts if isinstance(p, TextPart) and p.content]
            entries.append(
                HistoryMessage(
                    role='assistant',
                    text='\n'.join(text_parts) if text_parts else None,
                    timestamp=msg.timestamp or now,
                    conversation_id=conversation_id,
                    tool_calls=tc_list,
                )
            )

        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    # Persist the content as the model saw it. Oversized
                    # returns were already reduced at return time by
                    # OverflowingToolOutput (spill → preview + handle), so
                    # the persistence-side paste offload is gone; segments
                    # keep exactly what entered the run's history.
                    content = serialize_tool_content(part.content)
                    entries.append(
                        HistoryMessage(
                            role='tool',
                            text=content,
                            timestamp=part.timestamp or now,
                            conversation_id=conversation_id,
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            is_error=part.outcome == 'failed',
                        )
                    )
                elif isinstance(part, RetryPromptPart):
                    error_text = (
                        part.content if isinstance(part.content, str) else json.dumps(part.content, default=str)
                    )
                    entries.append(
                        HistoryMessage(
                            role='tool',
                            text=error_text,
                            timestamp=part.timestamp or now,
                            conversation_id=conversation_id,
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            is_error=True,
                        )
                    )

    return entries


def serialize_tool_content(content: object) -> str:
    """Convert tool return content to a string for storage."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)
