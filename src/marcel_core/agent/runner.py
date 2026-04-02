"""Agent runner — streams response tokens from Claude for one conversation turn.

Uses persistent ClaudeSDKClient sessions (via SessionManager) so conversation
context is maintained across turns with prompt cache reuse and SDK-managed
compaction.  Callers are responsible for persisting the audit log after
streaming completes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from marcel_core.agent.sessions import session_manager

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """Metadata returned after a complete turn."""

    total_cost_usd: float | None = None
    num_turns: int = 0
    session_id: str | None = None
    is_error: bool = False


async def stream_response(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str,
    model: str | None = None,
) -> AsyncIterator[str | TurnResult]:
    """Stream response tokens from Claude for one conversation turn.

    Uses a persistent ClaudeSDKClient session — the first call for a
    (user, conversation) pair creates and connects the client; subsequent
    calls reuse it with full prompt cache benefits.

    Yields:
        Text token strings as they arrive, then a final TurnResult with
        cost/usage metadata.

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

    got_stream_events = False
    pending_assistant_blocks: list[str] = []
    turn_result = TurnResult()

    async for msg in session.client.receive_response():
        if isinstance(msg, StreamEvent):
            event = msg.event
            if event.get('type') == 'content_block_delta':
                delta = event.get('delta', {})
                if delta.get('type') == 'text_delta':
                    text = delta.get('text', '')
                    if text:
                        got_stream_events = True
                        yield text

        elif isinstance(msg, AssistantMessage) and not got_stream_events:
            # Buffer complete text blocks as fallback if streaming never arrived
            for block in msg.content:
                if isinstance(block, TextBlock):
                    pending_assistant_blocks.append(block.text)

        elif isinstance(msg, ResultMessage):
            turn_result = TurnResult(
                total_cost_usd=msg.total_cost_usd,
                num_turns=msg.num_turns,
                session_id=msg.session_id,
                is_error=msg.is_error,
            )

    # Fallback: emit buffered text if no stream events were received
    if not got_stream_events:
        for text in pending_assistant_blocks:
            yield text

    yield turn_result
