"""Paste-backed OverflowStore — spilled tool returns land in the user's pastes.

The harness ``OverflowingToolOutput`` reduces an oversized tool return once,
at return time, and persists the reduced form; ``Spill`` writes the full
payload through an ``OverflowStore`` and hands the model a
``read_tool_result`` handle. Marcel's backend is the existing per-user,
content-addressed paste store — so spills obey the data-boundaries rule
(user content stays under ``~/.marcel/users/{slug}/``).

The store protocol carries no user context, so the runner stamps the turn's
user into a ``ContextVar`` before running the agent. Handles encode the
owning user (``'{slug}/{ref}'``) and reads validate the handle's owner
against the current turn's user — a model cannot read another user's
spill by crafting a handle. Runs with no stamped user (jobs until F9's
scoping, subagents) get no spill: the write raises and the band falls back
to its ``then`` action (truncation), which is the pre-harness behavior for
those paths.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from marcel_core.memory.pastes import retrieve_paste, store_paste

current_overflow_user: ContextVar[str | None] = ContextVar('current_overflow_user', default=None)
"""The user owning spills for the current task — set by ``stream_turn``."""


class PasteOverflowStore:
    """Implements the harness ``OverflowStore`` protocol over the paste store."""

    async def write(self, key: str, data: bytes) -> str:
        user_slug = current_overflow_user.get()
        if user_slug is None:
            raise RuntimeError('no turn user in context — spill unavailable, falling back')
        ref = store_paste(user_slug, data.decode('utf-8', errors='replace'))
        if not ref.startswith('sha256:'):
            # store_paste returns raw content below its own threshold — a
            # spill band configured under PASTE_THRESHOLD would mint broken
            # handles; refuse so the band falls back to truncation instead.
            raise RuntimeError('payload below the paste threshold — spill skipped, falling back')
        return f'{user_slug}/{ref}'

    async def read(self, handle: str) -> bytes:
        user_slug = current_overflow_user.get()
        owner, _, ref = handle.partition('/')
        if not owner or not ref or owner != user_slug:
            raise PermissionError('This result handle belongs to a different conversation.')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', ref):
            # The ref is model-controlled input: only a bare content hash is
            # legal. Path characters here are a traversal attempt out of the
            # owner's paste directory — refuse before touching disk.
            raise PermissionError('Malformed result handle.')
        content = retrieve_paste(owner, ref)
        if content is None:
            raise KeyError(f'No stored result for handle {handle!r} — it may have been cleaned up.')
        return content.encode('utf-8')
