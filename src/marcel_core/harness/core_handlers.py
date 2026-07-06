"""Core behaviours expressed as lifecycle event-bus handlers.

Behaviours that used to be bespoke code now ride the ``tool_call`` event
(:mod:`marcel_sdk.events`), proving the mechanism the whole epic depends on
and making the same gate available to extensions. Registered in order by
:func:`register_core_handlers` (first blocker wins):

1. **Self-modification path guard** — the runtime equivalent of the
   Claude-Code ``guard-restricted.py`` PreToolUse hook. Marcel's own
   ``write_file`` / ``edit_file`` tools had no restricted-path check
   (only role membership); this blocks a write to CLAUDE.md, the auth
   module, core config, or ``.env*`` unless the ``.claude/.unlock-safety``
   flag is present — the same paths and unlock procedure as the dev hook.

2. **Role-gating** — a second, harness-level layer behind the structural
   gate. :func:`~marcel_core.harness.agent.create_marcel_agent` already
   never *registers* an admin tool for a non-admin (the primary defense —
   the model cannot call a tool it cannot see). This handler blocks an
   admin tool call at execution time as defense-in-depth, and is the gate
   for any dynamically- or extension-registered admin tool that bypasses
   the registration-time filter. It is a harness enforcement point, not a
   role check inside a tool body — see ``.claude/rules/role-gating.md``.

3. **Command policy + human approval** (F2, ADR-260628-ca8f39) — a
   declarative allow/ask/deny policy over the command-execution surface
   (``bash`` / ``code_exec``). ``deny`` blocks; ``ask`` pauses and forwards
   a plain-language request to the user's channel, running only on an
   explicit allow (allow-once / allow-always), else expiring to deny.

Called from :func:`~marcel_core.harness.runner.stream_turn`.
"""

from __future__ import annotations

import logging
import os
import re

from marcel_core.harness.approval import ApprovalOutcome, approval_registry
from marcel_core.harness.command_policy import CommandPolicy, Verdict, default_policy
from marcel_sdk.events import EventBus, EventContext, ToolCallEvent

log = logging.getLogger(__name__)

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


# --- command policy + human approval (F2, ADR-260628-ca8f39) --------------

_POLICY: CommandPolicy | None = None


def command_policy() -> CommandPolicy:
    """Return the process-wide command policy.

    allow-always amendments persist in-process for the life of the kernel;
    every decision is recorded in the approval audit log regardless.
    """
    global _POLICY
    if _POLICY is None:
        _POLICY = default_policy()
    return _POLICY


def _summarize(event: ToolCallEvent, decision) -> str:
    """A plain-language one-liner describing the action, for the approval prompt."""
    command = str(event.args.get('command', '')).strip()
    if command:
        preview = command if len(command) <= 200 else command[:200] + '…'
        return f'{event.tool_name}: {preview}'
    return f'{event.tool_name} — {decision.reason}'


async def _request_approval(event: ToolCallEvent, ctx: EventContext, decision) -> ApprovalOutcome:
    """Forward an approval request to the user's channel and await the verdict.

    Safe default: if the channel cannot deliver an approval prompt (no
    ``send_approval_request``), or delivery fails, the action is denied —
    never auto-allowed.
    """
    from marcel_core.config import settings
    from marcel_core.plugin.channels import get_channel

    registry = approval_registry()
    request = registry.new_request(
        user_slug=ctx.user_slug,
        channel=ctx.channel,
        tool_name=event.tool_name,
        summary=_summarize(event, decision),
        args=dict(event.args),
    )

    plugin = get_channel(ctx.channel) if ctx.channel else None
    send = getattr(plugin, 'send_approval_request', None) if plugin is not None else None
    if send is None:
        log.warning(
            'channel %r cannot forward approvals — denying %r (safe default)',
            ctx.channel,
            event.tool_name,
        )
        return ApprovalOutcome.DENY

    try:
        delivered = await send(request.to_dict())
    except Exception:
        log.exception('failed to forward approval request on channel %r', ctx.channel)
        return ApprovalOutcome.DENY
    if not delivered:
        return ApprovalOutcome.DENY

    return await registry.wait(request, timeout=settings.marcel_approval_timeout_seconds)


async def _command_policy_handler(event: ToolCallEvent, ctx: EventContext) -> None:
    """Classify the action; deny, allow, or pause for human approval."""
    from marcel_core.config import settings

    if not settings.marcel_command_policy_enabled:
        return

    decision = command_policy().classify(event.tool_name, event.args)
    if decision.verdict is Verdict.ALLOW:
        return
    if decision.verdict is Verdict.DENY:
        event.deny(f'Blocked by command policy: {decision.reason}.')
        return

    # ASK — pause for human approval.
    outcome = await _request_approval(event, ctx, decision)
    if outcome is ApprovalOutcome.ALLOW_ALWAYS:
        command_policy().allow_always(event.tool_name, event.args)
        return
    if outcome is ApprovalOutcome.ALLOW_ONCE:
        return
    detail = (
        'no approval arrived in time — queued for later' if outcome is ApprovalOutcome.EXPIRED else 'the user declined'
    )
    event.deny(f'Command needs approval and {detail}: {decision.reason}.')


def register_core_handlers(bus: EventBus) -> None:
    """Register Marcel's built-in ``tool_call`` handlers on *bus*.

    Ordered so the self-mod guard (a hard safety boundary) runs first, then
    the role gate, then the command policy. A denial short-circuits the rest
    (first-blocker-wins), so the cheap hard gates run before the policy's
    (possibly approval-blocking) classification.
    """
    bus.on(ToolCallEvent.NAME, _self_mod_guard_handler)
    bus.on(ToolCallEvent.NAME, _role_gate_handler)
    bus.on(ToolCallEvent.NAME, _command_policy_handler)
