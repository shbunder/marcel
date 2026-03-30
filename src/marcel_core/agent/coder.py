"""Coder agent runner — spawns a Claude Code agent for code-change tasks.

Invoked when a Telegram user sends ``/code <request>``. Uses the ``claude_code``
tool preset for full file/shell/git capabilities and the system prompt preset
for automatic CLAUDE.md discovery.

Only one coder task runs at a time (global lock) to prevent concurrent git
operations.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, StreamEvent, TextBlock

log = logging.getLogger(__name__)

# Global lock — only one coder task at a time across all users.
_coder_lock = asyncio.Lock()

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

_CODER_APPEND = """\
You are being invoked via Telegram by a user who sent a /code command.
Follow the full feature development procedure in project/CLAUDE.md:
create an issue, implement, test, ship. Respond with the commit message
and a brief implementation summary when done. If you need clarification,
ask — the user will reply and you will be resumed.
"""


class CoderResult:
    """Outcome of a coder task."""

    __slots__ = ('response', 'session_id')

    def __init__(self, response: str, session_id: str | None) -> None:
        self.response = response
        self.session_id = session_id


async def run_coder_task(
    prompt: str,
    *,
    resume_session_id: str | None = None,
) -> CoderResult:
    """Run a coder task using the Claude Code preset.

    Acquires the global coder lock so only one task runs at a time.
    Returns the agent's text response and the SDK session ID (for resume).

    Args:
        prompt: The user's coding request (or follow-up message).
        resume_session_id: If continuing a previous coder session, the SDK
            session ID captured from the first run's StreamEvent.

    Returns:
        A :class:`CoderResult` with the response text and session ID.

    Raises:
        RuntimeError: If another coder task is already running.
    """
    if _coder_lock.locked():
        raise RuntimeError('A coder task is already running. Please wait for it to finish.')

    async with _coder_lock:
        return await _run_coder_task_inner(prompt, resume_session_id)


async def _run_coder_task_inner(
    prompt: str,
    resume_session_id: str | None,
) -> CoderResult:
    options = ClaudeAgentOptions(
        system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': _CODER_APPEND},
        tools={'type': 'preset', 'preset': 'claude_code'},
        cwd=_REPO_ROOT,
        permission_mode='bypassPermissions',
        max_turns=75,
        resume=resume_session_id,
    )

    session_id: str | None = None
    all_assistant_texts: list[str] = []
    msg_count = 0
    stream_text_parts: list[str] = []

    async for msg in claude_agent_sdk.query(prompt=prompt, options=options):
        msg_count += 1

        if isinstance(msg, StreamEvent):
            if session_id is None:
                session_id = msg.session_id

            # Collect streamed text deltas
            event = msg.event
            if event.get('type') == 'content_block_delta':
                delta = event.get('delta', {})
                if delta.get('type') == 'text_delta':
                    text = delta.get('text', '')
                    if text:
                        stream_text_parts.append(text)

        elif isinstance(msg, AssistantMessage):
            # Collect text from every AssistantMessage
            text_parts = [block.text for block in msg.content if isinstance(block, TextBlock)]
            if text_parts:
                all_assistant_texts.append(''.join(text_parts))

        else:
            log.warning('coder: unhandled message type %s', type(msg).__name__)

    # Prefer streamed text (complete incremental output), fall back to last
    # AssistantMessage text (final summary after multi-turn tool use).
    if stream_text_parts:
        response = ''.join(stream_text_parts)
    elif all_assistant_texts:
        response = all_assistant_texts[-1]
    else:
        response = ''

    log.warning(
        'coder finished: %d messages, stream_parts=%d, assistant_msgs=%d, response_len=%d',
        msg_count,
        len(stream_text_parts),
        len(all_assistant_texts),
        len(response),
    )

    return CoderResult(
        response=response,
        session_id=session_id,
    )
