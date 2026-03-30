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
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

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

    __slots__ = ('response', 'session_id', 'cost_usd', 'num_turns', 'is_error')

    def __init__(
        self,
        response: str,
        session_id: str | None,
        *,
        cost_usd: float | None = None,
        num_turns: int = 0,
        is_error: bool = False,
    ) -> None:
        self.response = response
        self.session_id = session_id
        self.cost_usd = cost_usd
        self.num_turns = num_turns
        self.is_error = is_error


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
    result_text: str | None = None
    result_cost: float | None = None
    result_turns: int = 0
    result_error: bool = False
    all_assistant_texts: list[str] = []

    async for msg in claude_agent_sdk.query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            # Final message from the claude_code preset — contains the result
            # text, session ID, cost, and turn count.
            session_id = msg.session_id
            result_text = msg.result
            result_cost = msg.total_cost_usd
            result_turns = msg.num_turns
            result_error = msg.is_error

        elif isinstance(msg, AssistantMessage):
            text_parts = [block.text for block in msg.content if isinstance(block, TextBlock)]
            if text_parts:
                all_assistant_texts.append(''.join(text_parts))

        elif isinstance(msg, StreamEvent):
            if session_id is None:
                session_id = msg.session_id

    # Prefer ResultMessage.result (authoritative), fall back to last
    # AssistantMessage text.
    if result_text:
        response = result_text
    elif all_assistant_texts:
        response = all_assistant_texts[-1]
    else:
        response = ''

    log.warning(
        'coder finished: turns=%d, cost=$%s, error=%s, response_len=%d',
        result_turns,
        f'{result_cost:.4f}' if result_cost else '?',
        result_error,
        len(response),
    )

    return CoderResult(
        response=response,
        session_id=session_id,
        cost_usd=result_cost,
        num_turns=result_turns,
        is_error=result_error,
    )
