"""Notification actions for the ``marcel`` tool.

Two public entry points:

- :func:`notify` — the action handler wired into the ``marcel`` dispatcher.
- :func:`send_notify` — an importable helper for other in-process tools that
  need to deliver a progress message mid-run (e.g. ``claude_code``).

Both route Telegram/job channels through the Telegram bot; other channels
fall through to the response stream.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)


async def send_notify(ctx: RunContext[MarcelDeps], message: str) -> str:
    """Public helper for sending notifications from other tools.

    Unlike :func:`notify` (the action handler), this takes a required
    message string and is importable by other modules.
    """
    return await notify(ctx, message)


async def notify(ctx: RunContext[MarcelDeps], message: str | None) -> str:
    """Send a short progress update to the user mid-task."""
    if not message:
        return 'ok'

    log.info('[marcel:notify] user=%s channel=%s msg=%s', ctx.deps.user_slug, ctx.deps.channel, message)

    # Mark that we sent a notification (so job executor can skip its own)
    ctx.deps.turn.notified = True

    # For Telegram (or background jobs that deliver to Telegram), send real-time notification
    if ctx.deps.channel in ('telegram', 'job'):
        try:
            from marcel_core.channels.telegram import bot, sessions
            from marcel_core.channels.telegram.formatting import markdown_to_telegram_html

            chat_id = sessions.get_chat_id(ctx.deps.user_slug)
            if chat_id:
                await bot.send_message(int(chat_id), markdown_to_telegram_html(message))
                return 'ok'
        except Exception as exc:
            log.warning('[marcel:notify] Telegram notification failed: %s', exc)
            return f'notify failed: {exc}'

    # For other channels, just log (they'll see it in the response stream)
    return 'ok'
