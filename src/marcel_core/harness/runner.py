"""Agent runner — streams events from pydantic-ai for one conversation turn.

Creates a stateless agent per turn, building context from segment-based
conversation history and dynamically selected memories.
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

from marcel_core.config import settings
from marcel_core.harness.agent import DEFAULT_MODEL, create_marcel_agent
from marcel_core.harness.context import MarcelDeps
from marcel_core.memory.conversation import (
    MAX_SUMMARY_CHARS,
    append_to_segment,
    load_latest_summary,
    read_active_segment,
)
from marcel_core.memory.history import HistoryMessage, ToolCall
from marcel_core.memory.pastes import PASTE_THRESHOLD, store_paste
from marcel_core.memory.summarizer import summarize_if_idle
from marcel_core.storage.settings import load_channel_model
from marcel_core.storage.users import get_user_role

log = logging.getLogger(__name__)

# Tool result preview length for the previous turn
_TOOL_RESULT_PREVIEW_LEN = 200

# Tools whose results should always be kept in full (regardless of age)
_ALWAYS_KEEP_TOOLS = frozenset({'memory_search', 'notify', 'conversation_search'})

# Aggressive tool lifecycle: only current turn (0) and previous turn (1).
_FULL_RESULT_TURNS = 1  # turn 0 = current
_PREVIEW_RESULT_TURNS = 2  # turn 1 = previous


def _tool_result_for_context(
    text: str | None,
    tool_name: str | None,
    turn_age: int,
) -> str:
    """Apply aggressive tool result lifecycle based on turn age.

    - Current turn (age 0): full result
    - Previous turn (age 1): 200-char preview
    - Older (age 2+): inline name-only note

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

    # Current turn: full result
    if turn_age < _FULL_RESULT_TURNS:
        return text

    # Previous turn: short preview
    if turn_age < _PREVIEW_RESULT_TURNS:
        if len(text) > _TOOL_RESULT_PREVIEW_LEN:
            return text[:_TOOL_RESULT_PREVIEW_LEN] + f'\n... ({len(text)} chars total, truncated)'
        return text

    # Older turns: name-only note
    return f'[Used {tool_name or "tool"}]'


def _messages_to_model(
    messages: list[HistoryMessage],
    num_turns: int | None = None,
) -> list[ModelMessage]:
    """Convert internal HistoryMessage objects to pydantic-ai ModelMessage format.

    Handles user, assistant (with tool calls), and tool result messages.
    Applies aggressive tool lifecycle trimming based on turn age.

    Args:
        messages: The history messages to convert.
        num_turns: Total number of turns for age calculation.
                   If None, count from the messages themselves.
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


async def build_context(
    user_slug: str,
    channel: str,
) -> list[ModelMessage]:
    """Build the context window for a conversation turn.

    Loads the rolling summary (if any) and active segment messages,
    applies tool lifecycle trimming, and returns ModelMessage list.

    1. Check for idle summarization (seals segment if idle >1 hour)
    2. Load latest summary from sealed segments
    3. Load active segment messages
    4. Apply tool result lifecycle
    5. Prepend summary as context
    """
    # 1. Check for idle summarization before building context
    idle_minutes = settings.marcel_idle_summarize_minutes
    summarized = await summarize_if_idle(user_slug, channel, idle_minutes)
    if summarized:
        log.info('%s-%s: idle summarization completed before turn', user_slug, channel)

    # 2. Load latest summary
    latest_summary = load_latest_summary(user_slug, channel)

    # 3. Load active segment messages
    active_messages = read_active_segment(user_slug, channel)

    # 4. Convert to model messages with tool lifecycle applied
    model_messages = _messages_to_model(active_messages)

    # 5. Prepend summary as context if it exists
    if latest_summary:
        summary_text = latest_summary.summary
        # Cap summary to avoid blowing the token budget
        if len(summary_text) > MAX_SUMMARY_CHARS:
            summary_text = summary_text[:MAX_SUMMARY_CHARS] + '\n... (summary truncated)'
        summary_msg = ModelRequest(parts=[UserPromptPart(content=f'[Previous conversation summary: {summary_text}]')])
        model_messages.insert(0, summary_msg)

    return model_messages


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

    # Build context from continuous conversation (handles idle summarization)
    message_history = await build_context(user_slug, channel)

    # Append user message to segment (after loading context, so it's not duplicated)
    user_msg = HistoryMessage(
        role='user',
        text=user_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_to_segment(user_slug, channel, user_msg)

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
            log.info('%s-%s: stream started model=%s', user_slug, channel, resolved_model)
            async for text_delta in result.stream_text(delta=True, debounce_by=0.01):
                if text_delta:
                    yield TextDelta(text=text_delta)
                    assistant_text_parts.append(text_delta)

            # Wait for full completion (runs on_complete, processes trailing tool calls)
            await result.get_output()
            log.debug('%s-%s: stream finished', user_slug, channel)

            # Capture all messages for tool call extraction
            all_messages = result.all_messages()

            usage = result.usage()
            if usage and usage.total_tokens:
                log.info(
                    '%s-%s: turn complete — %d tokens (in: %d, out: %d, requests: %d)',
                    user_slug,
                    channel,
                    usage.total_tokens,
                    usage.request_tokens,
                    usage.response_tokens,
                    usage.requests,
                )

    except Exception as exc:
        log.exception('%s-%s: turn execution failed', user_slug, channel)
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
            append_to_segment(user_slug, channel, entry)
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

    # Save final assistant text response to segment
    assistant_text = ''.join(assistant_text_parts)
    if assistant_text:
        assistant_msg = HistoryMessage(
            role='assistant',
            text=assistant_text,
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id=conversation_id,
        )
        append_to_segment(user_slug, channel, assistant_msg)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
