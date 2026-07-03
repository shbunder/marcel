"""Core behaviours expressed as lifecycle event-bus handlers.

Two behaviours that used to be bespoke code now ride the ``tool_call``
event (:mod:`marcel_sdk.events`), proving the mechanism the whole epic
depends on and making the same gate available to extensions:

1. **Role-gating** — a second, harness-level layer behind the structural
   gate. :func:`~marcel_core.harness.agent.create_marcel_agent` already
   never *registers* an admin tool for a non-admin (the primary defense —
   the model cannot call a tool it cannot see). This handler blocks an
   admin tool call at execution time as defense-in-depth, and is the gate
   for any dynamically- or extension-registered admin tool that bypasses
   the registration-time filter. It is a harness enforcement point, not a
   role check inside a tool body — see ``.claude/rules/role-gating.md``.

2. **Self-modification path guard** — the runtime equivalent of the
   Claude-Code ``guard-restricted.py`` PreToolUse hook. Marcel's own
   ``write_file`` / ``edit_file`` tools had no restricted-path check
   (only role membership); this blocks a write to CLAUDE.md, the auth
   module, core config, or ``.env*`` unless the ``.claude/.unlock-safety``
   flag is present — the same paths and unlock procedure as the dev hook.

Both are registered on the turn's bus by :func:`register_core_handlers`,
called from :func:`~marcel_core.harness.runner.stream_turn`.
"""

from __future__ import annotations

import os
import re

from marcel_sdk.events import EventBus, EventContext, ToolCallEvent

# --- self-mod path guard: mirror .claude/hooks/guard-restricted.py --------

_UNLOCK_FLAG = '.claude/.unlock-safety'
"""Presence of this file (relative to cwd) lifts the restricted-path block —
the same escape hatch the Claude-Code dev hook uses."""

_PATH_WRITING_TOOLS = frozenset({'write_file', 'edit_file'})
"""Tools whose ``path`` argument is checked against the restricted set."""

_RESTRICTED: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'(^|/)CLAUDE\.md$'), 'project instructions (CLAUDE.md)'),
    (re.compile(r'(^|/)src/marcel_core/auth/'), 'auth module'),
    (re.compile(r'(^|/)src/marcel_core/config\.py$'), 'core config'),
    (re.compile(r'(^|/)\.env(\.|$)'), 'environment file'),
)


def _restricted_reason(path: str) -> str | None:
    """Return the restriction label if *path* is restricted, else ``None``.

    Checks the raw path and its realpath (a symlink cannot dodge the
    guard), matching the dev hook's behaviour.
    """
    candidates = {path}
    try:
        candidates.add(os.path.realpath(path))
    except OSError:
        pass
    for pattern, label in _RESTRICTED:
        if any(pattern.search(c) for c in candidates):
            return label
    return None


def _self_mod_guard_handler(event: ToolCallEvent, ctx: EventContext) -> None:
    """Block a write to a restricted path unless the unlock flag is present."""
    if event.tool_name not in _PATH_WRITING_TOOLS:
        return
    path = event.args.get('path') or ''
    if not path:
        return
    if os.path.exists(_UNLOCK_FLAG):
        return
    reason = _restricted_reason(path)
    if reason is not None:
        event.deny(
            f'Blocked write to restricted path {path!r}: {reason}. '
            f'This is a Marcel self-modification safety rule. To unlock, an '
            f'admin creates {_UNLOCK_FLAG}, makes the change, commits, and '
            f'removes the flag.',
        )


# --- role gating: defense-in-depth over the structural gate ---------------


def _role_gate_handler(event: ToolCallEvent, ctx: EventContext) -> None:
    """Block an admin-tier tool call made in a non-admin turn."""
    # Imported here (not at module top) to avoid an import cycle: agent.py
    # pulls in the whole tool suite, and runner.py imports both.
    from marcel_core.harness.agent import admin_tool_names

    if ctx.role != 'admin' and event.tool_name in admin_tool_names():
        event.deny(
            f'The {event.tool_name!r} tool requires admin privileges; this session is not an admin session.',
        )


def register_core_handlers(bus: EventBus) -> None:
    """Register Marcel's built-in ``tool_call`` handlers on *bus*.

    Ordered so the self-mod guard (a hard safety boundary) runs before the
    role gate. Either denying short-circuits the rest (first-blocker-wins).
    """
    bus.on(ToolCallEvent.NAME, _self_mod_guard_handler)
    bus.on(ToolCallEvent.NAME, _role_gate_handler)
