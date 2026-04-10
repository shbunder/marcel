"""Agent runner — streams events from pydantic-ai for one conversation turn.

Replaces the old agent/runner.py which used ClaudeSDKClient sessions.
This version creates a stateless agent per turn, building context from
JSONL history and dynamically selected memories.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from marcel_core.harness.agent import DEFAULT_MODEL, create_marcel_agent
from marcel_core.harness.context import MarcelDeps, _host_home
from marcel_core.memory.history import HistoryMessage, append_message, read_recent_turns
from marcel_core.storage.settings import load_channel_model
from marcel_core.storage.users import get_user_role

log = logging.getLogger(__name__)

# Number of recent turns to load as conversation context, per channel.
# Telegram sessions are long-lived, so we load a generous window.
_HISTORY_TURNS: dict[str, int] = {
    'telegram': 50,
    'cli': 20,
}
_DEFAULT_HISTORY_TURNS = 20


def history_to_messages(messages: list[HistoryMessage]) -> list[ModelMessage]:
    """Convert internal HistoryMessage objects to pydantic-ai ModelMessage format.

    Only user and assistant messages are converted — tool and system messages
    are skipped since we don't track full tool call/response round-trips in
    the JSONL history yet.
    """
    result: list[ModelMessage] = []
    for msg in messages:
        if not msg.text:
            continue
        if msg.role == 'user':
            result.append(ModelRequest(parts=[UserPromptPart(content=msg.text, timestamp=msg.timestamp)]))
        elif msg.role == 'assistant':
            result.append(ModelResponse(parts=[TextPart(content=msg.text)], timestamp=msg.timestamp))
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
        MarcelEvent instances: RunStarted, TextDelta, RunFinished.
        ToolCallStarted/Completed are reserved for future implementation via agent.iter().
    """
    role = get_user_role(user_slug)

    # For admin users on non-CLI channels, default cwd to the user's home directory
    # ($HOME is bind-mounted at the same path as on the host, so this IS the server home).
    # For CLI sessions, cwd comes from the client's current directory.
    effective_cwd = cwd
    if role == 'admin' and not effective_cwd and channel != 'cli':
        effective_cwd = _host_home()

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
    message_history = history_to_messages(prior_messages)

    # Append user message to history (after loading, so it's not duplicated)
    user_msg = HistoryMessage(
        role='user',
        text=user_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_message(user_slug, user_msg)

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

    try:
        # NOTE: Do NOT pass event_stream_handler to run_stream() — pydantic-ai docs
        # explicitly recommend against mixing event_stream_handler with stream_text().
        # Tool call visibility will be added via agent.iter() in a future iteration.
        async with agent.run_stream(user_text, deps=deps, message_history=message_history) as result:
            log.debug('[runner] stream started for user=%s', user_slug)
            async for text_delta in result.stream_text(delta=True, debounce_by=0.01):
                if text_delta:
                    yield TextDelta(text=text_delta)
                    assistant_text_parts.append(text_delta)

            # Wait for full completion (runs on_complete, processes trailing tool calls)
            await result.get_output()
            log.debug('[runner] stream finished for user=%s', user_slug)

            usage = result.usage()
            if usage and usage.total_tokens:
                log.debug(
                    'Turn complete: %d tokens (input: %d, output: %d)',
                    usage.total_tokens,
                    usage.request_tokens,
                    usage.response_tokens,
                )

    except Exception as exc:
        log.exception('[runner] turn execution failed for user=%s', user_slug)
        is_error = True
        error_text = f'Error: {exc}'
        yield TextDelta(text=error_text)
        assistant_text_parts.append(error_text)

    # Save assistant message to history
    assistant_text = ''.join(assistant_text_parts)
    assistant_msg = HistoryMessage(
        role='assistant',
        text=assistant_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_message(user_slug, assistant_msg)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
