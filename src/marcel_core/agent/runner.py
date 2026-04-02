"""Agent runner — streams AG-UI events from Claude for one conversation turn.

Uses persistent ClaudeSDKClient sessions (via SessionManager) so conversation
context is maintained across turns with prompt cache reuse and SDK-managed
compaction.  Callers are responsible for persisting the audit log after
streaming completes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolResultBlock

from marcel_core.agent.events import (
    AgentEvent,
    RunFinished,
    RunStarted,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
    _truncate,
)
from marcel_core.agent.sessions import session_manager

log = logging.getLogger(__name__)


async def stream_response(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    *,
    model: str | None = None,
) -> AsyncIterator[AgentEvent]:
    """Stream AG-UI events from Claude for one conversation turn.

    Uses a persistent ClaudeSDKClient session — the first call for a
    (user, conversation) pair creates and connects the client; subsequent
    calls reuse it with full prompt cache benefits.

    Yields:
        AgentEvent instances: RunStarted, TextMessageStart/Content/End,
        ToolCallStart/End/Result, and a final RunFinished.

    Args:
        user_slug: The user's slug.
        channel: The originating channel (affects system prompt formatting).
        user_text: The user's message for this turn.
        conversation_id: The active conversation identifier.
        model: Optional model override.
    """
    session = await session_manager.get_or_create(
        user_slug,
        conversation_id,
        channel,
        model,
    )

    await session.client.query(user_text)

    yield RunStarted(thread_id=conversation_id)

    got_stream_events = False
    text_started = False
    pending_assistant_blocks: list[str] = []
    pending_tool_results: list[ToolCallResult] = []
    run_finished = RunFinished()

    # Track active tool_use content blocks by index
    active_tool_blocks: dict[int, str] = {}

    async for msg in session.client.receive_response():
        if isinstance(msg, StreamEvent):
            event = msg.event
            event_type = event.get('type')

            if event_type == 'content_block_start':
                cb = event.get('content_block', {})
                if cb.get('type') == 'tool_use':
                    if text_started:
                        yield TextMessageEnd()
                        text_started = False
                    idx = event.get('index', 0)
                    tool_id = cb.get('id', '')
                    tool_name = cb.get('name', '')
                    active_tool_blocks[idx] = tool_id
                    yield ToolCallStart(tool_call_id=tool_id, tool_name=tool_name)

            elif event_type == 'content_block_delta':
                delta = event.get('delta', {})
                if delta.get('type') == 'text_delta':
                    text = delta.get('text', '')
                    if text:
                        got_stream_events = True
                        if not text_started:
                            yield TextMessageStart()
                            text_started = True
                        yield TextMessageContent(text=text)

            elif event_type == 'content_block_stop':
                idx = event.get('index', 0)
                if tool_id := active_tool_blocks.pop(idx, None):
                    yield ToolCallEnd(tool_call_id=tool_id)

        elif isinstance(msg, AssistantMessage):
            if not got_stream_events:
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        pending_assistant_blocks.append(block.text)
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    summary = _truncate(str(block.content or ''))
                    pending_tool_results.append(
                        ToolCallResult(
                            tool_call_id=block.tool_use_id,
                            is_error=bool(block.is_error),
                            summary=summary,
                        )
                    )

        elif isinstance(msg, ResultMessage):
            run_finished = RunFinished(
                total_cost_usd=msg.total_cost_usd,
                num_turns=msg.num_turns,
                session_id=msg.session_id,
                is_error=msg.is_error,
            )

    # Close any open text message
    if text_started:
        yield TextMessageEnd()

    # Fallback: emit buffered text if no stream events were received
    if not got_stream_events and pending_assistant_blocks:
        yield TextMessageStart()
        for text in pending_assistant_blocks:
            yield TextMessageContent(text=text)
        yield TextMessageEnd()

    for result in pending_tool_results:
        yield result

    yield run_finished
