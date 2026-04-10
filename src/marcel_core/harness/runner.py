"""Agent runner — streams events from pydantic-ai for one conversation turn.

Replaces the old agent/runner.py which used ClaudeSDKClient sessions.
This version creates a stateless agent per turn, building context from
JSONL history and dynamically selected memories.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic_ai import UsageLimits
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

from marcel_core.harness.agent import DEFAULT_MODEL, create_marcel_agent
from marcel_core.harness.context import MarcelDeps
from marcel_core.memory.history import HistoryMessage, ToolCall, append_message, read_recent_turns
from marcel_core.memory.pastes import PASTE_THRESHOLD, store_paste
from marcel_core.storage.settings import load_channel_model
from marcel_core.storage.users import get_user_role

log = logging.getLogger(__name__)

# Number of recent turns to load as conversation context, per channel.
_HISTORY_TURNS: dict[str, int] = {
    'telegram': 15,
    'cli': 20,
}
_DEFAULT_HISTORY_TURNS = 15

# Tool result preview length for older turns
_TOOL_RESULT_PREVIEW_LEN = 800

# Tools whose results should always be kept in full (regardless of age)
_ALWAYS_KEEP_TOOLS = frozenset({'memory_search', 'notify'})

# Turns threshold: results in the last N turns are kept in full,
# older turns get previews, very old turns get names-only.
_FULL_RESULT_TURNS = 3
_PREVIEW_RESULT_TURNS = 8


def _tool_result_for_context(
    text: str | None,
    tool_name: str | None,
    turn_age: int,
) -> str:
    """Apply tiered trimming to a tool result based on turn age.

    Args:
        text: The tool result content.
        tool_name: The tool that produced this result.
        turn_age: How many turns ago this result was produced (0 = current turn).

    Returns:
        The (possibly trimmed) result string for inclusion in context.
    """
    if not text:
        return f'({tool_name or "tool"} completed with no output)'

    # Always keep results for certain tools
    if tool_name and tool_name in _ALWAYS_KEEP_TOOLS:
        return text

    # Recent turns: full result
    if turn_age < _FULL_RESULT_TURNS:
        return text

    # Medium-age turns: preview
    if turn_age < _PREVIEW_RESULT_TURNS:
        if len(text) > _TOOL_RESULT_PREVIEW_LEN:
            return text[:_TOOL_RESULT_PREVIEW_LEN] + f'\n... ({len(text)} chars total, truncated)'
        return text

    # Old turns: name-only summary
    preview = text[:200] + '...' if len(text) > 200 else text
    return f'[{tool_name or "tool"} result: {preview}]'


def history_to_messages(messages: list[HistoryMessage], num_turns: int | None = None) -> list[ModelMessage]:
    """Convert internal HistoryMessage objects to pydantic-ai ModelMessage format.

    Handles user, assistant (with tool calls), and tool result messages.
    Applies tiered trimming to tool results based on turn age.

    Args:
        messages: The history messages to convert.
        num_turns: Total number of turns being loaded (for age calculation).
                   If None, all results are kept in full.
    """
    # Count turns (user messages) to compute age for tiered trimming
    turn_count = sum(1 for m in messages if m.role == 'user') if num_turns is None else num_turns
    current_turn = 0

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
            current_turn += 1
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
            turn_age = turn_count - current_turn
            content = _tool_result_for_context(msg.text, msg.tool_name, turn_age)
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


@dataclass
class MarcelEvent:
    """Base class for events streamed during a turn."""

    type: str


@dataclass
class RunStarted(MarcelEvent):
    """Turn execution started."""

    type: Literal['run_started'] = 'run_started'  # type: ignore[assignment]
    conversation_id: str = ''


@dataclass
class TextDelta(MarcelEvent):
    """Incremental text from assistant."""

    type: Literal['text_delta'] = 'text_delta'  # type: ignore[assignment]
    text: str = ''


@dataclass
class ToolCallStarted(MarcelEvent):
    """Tool invocation started."""

    type: Literal['tool_call_started'] = 'tool_call_started'  # type: ignore[assignment]
    tool_call_id: str = ''
    tool_name: str = ''


@dataclass
class ToolCallCompleted(MarcelEvent):
    """Tool invocation completed."""

    type: Literal['tool_call_completed'] = 'tool_call_completed'  # type: ignore[assignment]
    tool_call_id: str = ''
    tool_name: str = ''
    result: str = ''
    is_error: bool = False


@dataclass
class RunFinished(MarcelEvent):
    """Turn execution finished."""

    type: Literal['run_finished'] = 'run_finished'  # type: ignore[assignment]
    total_cost_usd: float | None = None
    is_error: bool = False


def _extract_tool_history(
    all_messages: list[ModelMessage],
    user_slug: str,
    conversation_id: str,
) -> list[HistoryMessage]:
    """Extract tool call and result history from pydantic-ai messages.

    Walks the message list produced by ``result.all_messages()`` and converts
    tool-related parts into HistoryMessage entries for JSONL storage.
    Large tool results are offloaded to the paste store.

    Returns assistant messages with tool_calls and tool-role result messages.
    Skips the initial user prompt and final text-only response (handled by caller).
    """
    entries: list[HistoryMessage] = []
    now = datetime.now(tz=timezone.utc)

    for msg in all_messages:
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
                    # Serialize content to string
                    content = _serialize_tool_content(part.content)
                    # Offload large results to paste store
                    result_ref = None
                    if len(content) >= PASTE_THRESHOLD:
                        result_ref = store_paste(user_slug, content)
                        # Keep a preview in text for scanning
                        content = content[:_TOOL_RESULT_PREVIEW_LEN]

                    entries.append(
                        HistoryMessage(
                            role='tool',
                            text=content,
                            timestamp=part.timestamp or now,
                            conversation_id=conversation_id,
                            tool_call_id=part.tool_call_id,
                            tool_name=part.tool_name,
                            result_ref=result_ref,
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


def _serialize_tool_content(content: object) -> str:
    """Convert tool return content to a string for storage."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


async def stream_turn(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
) -> AsyncIterator[MarcelEvent]:
    """Stream events from a single conversation turn.

    Creates a stateless agent with context from JSONL history and memories.
    Yields MarcelEvent objects for the channel to handle.

    Args:
        user_slug: The user's slug.
        channel: The originating channel.
        user_text: The user's message for this turn.
        conversation_id: The active conversation identifier.
        model: Optional model override (e.g., 'openai:gpt-4').

    Yields:
        MarcelEvent instances: RunStarted, TextDelta, ToolCallStarted,
        ToolCallCompleted, RunFinished.
    """
    role = get_user_role(user_slug)

    # For admin users on non-CLI channels, default cwd to the user's home directory.
    # For CLI sessions, cwd comes from the client's current directory.
    effective_cwd = cwd
    if role == 'admin' and not effective_cwd and channel != 'cli':
        effective_cwd = str(Path.home())

    deps = MarcelDeps(
        user_slug=user_slug,
        conversation_id=conversation_id,
        channel=channel,
        model=model,
        role=role,
        cwd=effective_cwd,
    )

    # Load prior conversation history BEFORE appending the current message
    num_turns = _HISTORY_TURNS.get(channel, _DEFAULT_HISTORY_TURNS)
    prior_messages = read_recent_turns(user_slug, conversation_id, num_turns=num_turns)
    message_history = history_to_messages(prior_messages, num_turns=num_turns)

    # Append user message to history (after loading, so it's not duplicated)
    user_msg = HistoryMessage(
        role='user',
        text=user_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_message(user_slug, user_msg, channel=channel)

    # Build system prompt with context (async version includes AI-selected memories)
    from marcel_core.harness.context import build_instructions_async

    system_prompt = await build_instructions_async(deps, query=user_text)

    # Resolve model: explicit override > per-channel setting > default
    resolved_model = model or load_channel_model(user_slug, channel) or DEFAULT_MODEL

    # Create agent with role-appropriate tool set
    agent = create_marcel_agent(resolved_model, system_prompt=system_prompt, role=role)

    yield RunStarted(conversation_id=conversation_id)

    assistant_text_parts: list[str] = []
    is_error = False
    total_cost = None
    all_messages: list[ModelMessage] = []

    try:
        async with agent.run_stream(
            user_text,
            deps=deps,
            message_history=message_history,
            usage_limits=UsageLimits(request_limit=15),
        ) as result:
            log.debug('[runner] stream started for user=%s', user_slug)
            async for text_delta in result.stream_text(delta=True, debounce_by=0.01):
                if text_delta:
                    yield TextDelta(text=text_delta)
                    assistant_text_parts.append(text_delta)

            # Wait for full completion (runs on_complete, processes trailing tool calls)
            await result.get_output()
            log.debug('[runner] stream finished for user=%s', user_slug)

            # Capture all messages for tool call extraction
            all_messages = result.all_messages()

            usage = result.usage()
            if usage and usage.total_tokens:
                log.info(
                    'Turn complete: %d tokens (input: %d, output: %d, requests: %d)',
                    usage.total_tokens,
                    usage.request_tokens,
                    usage.response_tokens,
                    usage.requests,
                )

    except Exception as exc:
        log.exception('[runner] turn execution failed for user=%s', user_slug)
        is_error = True
        error_text = f'Error: {exc}'
        yield TextDelta(text=error_text)
        assistant_text_parts.append(error_text)

    # Extract and save tool call history from the pydantic-ai message trace.
    # This captures intermediate tool calls (assistant→tool→assistant loops)
    # that happen during a single turn, before the final text response.
    if all_messages:
        tool_entries = _extract_tool_history(all_messages, user_slug, conversation_id)
        for entry in tool_entries:
            append_message(user_slug, entry, channel=channel)
            # Yield events for tool calls so channels can show progress
            if entry.role == 'assistant' and entry.tool_calls:
                for tc in entry.tool_calls:
                    yield ToolCallStarted(tool_call_id=tc.id, tool_name=tc.name)
            elif entry.role == 'tool':
                yield ToolCallCompleted(
                    tool_call_id=entry.tool_call_id or '',
                    tool_name=entry.tool_name or '',
                    result=entry.text or '',
                    is_error=entry.is_error,
                )

    # Save final assistant text response to history
    assistant_text = ''.join(assistant_text_parts)
    if assistant_text:
        assistant_msg = HistoryMessage(
            role='assistant',
            text=assistant_text,
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id=conversation_id,
        )
        append_message(user_slug, assistant_msg, channel=channel)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
