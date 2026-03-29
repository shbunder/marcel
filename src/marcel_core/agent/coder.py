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
from collections.abc import AsyncIterator
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, StreamEvent, TextBlock
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

log = logging.getLogger(__name__)

# Global lock — only one coder task at a time across all users.
_coder_lock = asyncio.Lock()

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

# Paths that the coder agent must never write to.
_RESTRICTED_PATTERNS: tuple[str, ...] = (
    '/CLAUDE.md',
    'src/marcel_core/auth/',
)

_CODER_APPEND = """\
You are being invoked via Telegram by a user who sent a /code command.
Follow the full feature development procedure in project/CLAUDE.md:
create an issue, implement, test, ship. Respond with the commit message
and a brief implementation summary when done. If you need clarification,
ask — the user will reply and you will be resumed.
"""


async def _restricted_file_guard(
    tool_name: str,
    tool_input: dict[str, object],
    _ctx: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Deny Write/Edit calls targeting restricted files."""
    if tool_name in ('Write', 'Edit'):
        file_path = str(tool_input.get('file_path', ''))
        for pattern in _RESTRICTED_PATTERNS:
            if pattern in file_path:
                return PermissionResultDeny(
                    message=f'Restricted path: {file_path} — CLAUDE.md and auth files are off-limits.',
                )
    return PermissionResultAllow()


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


async def _as_prompt_stream(text: str) -> AsyncIterator[dict[str, Any]]:
    """Wrap a plain string into an async iterable of message dicts.

    The SDK requires an AsyncIterable prompt (streaming mode) when
    ``can_use_tool`` is set.
    """
    yield {'role': 'user', 'content': text}


async def _run_coder_task_inner(
    prompt: str,
    resume_session_id: str | None,
) -> CoderResult:
    options = ClaudeAgentOptions(
        system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': _CODER_APPEND},
        tools={'type': 'preset', 'preset': 'claude_code'},
        cwd=_REPO_ROOT,
        permission_mode='bypassPermissions',
        can_use_tool=_restricted_file_guard,
        max_turns=75,
        resume=resume_session_id,
    )

    session_id: str | None = None
    last_assistant_text: str = ''
    msg_count = 0

    async for msg in claude_agent_sdk.query(prompt=_as_prompt_stream(prompt), options=options):
        msg_count += 1
        msg_type = type(msg).__name__

        if isinstance(msg, StreamEvent):
            if session_id is None:
                session_id = msg.session_id
            event_type = msg.event.get('type', '?')
            log.debug('coder msg #%d: StreamEvent type=%s', msg_count, event_type)

        elif isinstance(msg, AssistantMessage):
            block_types = [type(b).__name__ for b in msg.content]
            text_parts = [block.text for block in msg.content if isinstance(block, TextBlock)]
            log.info(
                'coder msg #%d: AssistantMessage blocks=%s text_len=%d',
                msg_count,
                block_types,
                sum(len(t) for t in text_parts),
            )
            if text_parts:
                last_assistant_text = ''.join(text_parts)

        else:
            log.info('coder msg #%d: %s (unhandled)', msg_count, msg_type)

    log.info('coder finished: %d messages, response_len=%d, session_id=%s', msg_count, len(last_assistant_text), session_id)

    return CoderResult(
        response=last_assistant_text,
        session_id=session_id,
    )
