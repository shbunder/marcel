"""Agent runner — streams response tokens from Claude for one conversation turn.

Callers are responsible for persisting the turn after streaming completes.
Memory extraction is scheduled as a background task by the API layer (api/chat.py).
"""

from collections.abc import AsyncIterator

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, StreamEvent, TextBlock

from marcel_core.agent.context import build_system_prompt
from marcel_core.skills import build_skills_mcp_server


async def stream_response(
    user_slug: str,
    channel: str,
    user_text: str,
    conversation_id: str | None = None,
    model: str | None = None,
) -> AsyncIterator[str]:
    """Stream response tokens from Claude for one conversation turn.

    Yields individual text tokens as they arrive via streaming events.
    Falls back to yielding complete AssistantMessage text blocks if no
    streaming events are received (e.g. in some test environments).

    Args:
        user_slug: The user's slug.
        channel: The originating channel (affects system prompt formatting).
        user_text: The user's message for this turn.
        conversation_id: Filename stem of the current conversation for context loading.

    Yields:
        Text token strings as they arrive from Claude.
    """
    system_prompt = build_system_prompt(user_slug, channel, conversation_id)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={'skills': build_skills_mcp_server(user_slug)},
        allowed_tools=['cmd'],
        permission_mode='default',
        max_turns=1,
        model=model,
    )

    got_stream_events = False

    async for msg in claude_agent_sdk.query(prompt=user_text, options=options):
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
            # Fallback: emit complete text blocks if streaming events never arrived
            for block in msg.content:
                if isinstance(block, TextBlock):
                    yield block.text
