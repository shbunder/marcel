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

from marcel_core.harness.agent import create_marcel_agent
from marcel_core.harness.context import MarcelDeps
from marcel_core.memory.history import HistoryMessage, ToolCall, append_message

log = logging.getLogger(__name__)


@dataclass
class MarcelEvent:
    """Base class for events streamed during a turn."""

    type: str


@dataclass
class RunStarted(MarcelEvent):
    """Turn execution started."""

    type: Literal['run_started'] = 'run_started'
    conversation_id: str = ''


@dataclass
class TextDelta(MarcelEvent):
    """Incremental text from assistant."""

    type: Literal['text_delta'] = 'text_delta'
    text: str = ''


@dataclass
class ToolCallStarted(MarcelEvent):
    """Tool invocation started."""

    type: Literal['tool_call_started'] = 'tool_call_started'
    tool_call_id: str = ''
    tool_name: str = ''


@dataclass
class ToolCallCompleted(MarcelEvent):
    """Tool invocation completed."""

    type: Literal['tool_call_completed'] = 'tool_call_completed'
    tool_call_id: str = ''
    tool_name: str = ''
    result: str = ''
    is_error: bool = False


@dataclass
class RunFinished(MarcelEvent):
    """Turn execution finished."""

    type: Literal['run_finished'] = 'run_finished'
    total_cost_usd: float | None = None
    is_error: bool = False


async def stream_turn(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    *,
    model: str | None = None,
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
        MarcelEvent instances: RunStarted, TextDelta, ToolCallStarted/Completed, RunFinished.
    """
    deps = MarcelDeps(
        user_slug=user_slug,
        conversation_id=conversation_id,
        channel=channel,
        model=model,
    )

    # Append user message to history
    user_msg = HistoryMessage(
        role='user',
        text=user_text,
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )
    append_message(user_slug, user_msg)

    # Build system prompt with context
    from marcel_core.harness.context import build_instructions

    system_prompt = build_instructions(deps)

    # Create agent with system prompt
    agent = create_marcel_agent(model or 'claude-sonnet-4-6', system_prompt=system_prompt)

    # TODO: Load conversation context from history
    # For now, just use the user prompt
    # In Phase 2, we'll add memory selection and history context

    yield RunStarted(conversation_id=conversation_id)

    # Stream agent response
    assistant_text_parts: list[str] = []
    tool_calls_made: list[ToolCall] = []
    is_error = False
    total_cost = None

    try:
        async with agent.run_stream(user_text, deps=deps) as result:
            # Stream text deltas
            async for text_delta in result.stream_text(delta=True, debounce_by=0.01):
                if text_delta:
                    yield TextDelta(text=text_delta)
                    assistant_text_parts.append(text_delta)

            # Get final result (waits for completion)
            final_output = await result.get_output()

            # Extract usage/cost if available
            usage = result.usage()
            if usage and usage.total_tokens:
                log.debug(
                    'Turn complete: %d tokens (input: %d, output: %d)',
                    usage.total_tokens,
                    usage.request_tokens,
                    usage.response_tokens,
                )
                # TODO: Calculate cost from usage

    except Exception as exc:
        log.exception('Turn execution failed')
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
        tool_calls=tool_calls_made if tool_calls_made else None,
    )
    append_message(user_slug, assistant_msg)

    yield RunFinished(total_cost_usd=total_cost, is_error=is_error)
