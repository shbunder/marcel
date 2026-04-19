"""Telegram channel integration for Marcel.

Exposes a FastAPI router that receives webhook updates from the Telegram
Bot API and routes them through the Marcel agent loop.

Self-registers with :mod:`marcel_core.plugin.channels` at import time so the
kernel can resolve the channel uniformly through the plugin registry instead
of via direct imports.

Setup summary:
1. Create a bot via @BotFather and copy the token.
2. Set ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_USER_MAP`` in ``.env``.
3. Register the webhook: ``python -m marcel_core.channels.telegram.setup <public_url>``.

See ``docs/channels/telegram.md`` for the full setup guide.
"""

from dataclasses import dataclass

from fastapi import APIRouter

from marcel_core.channels.adapter import ChannelCapabilities
from marcel_core.plugin import register_channel

from .webhook import router


@dataclass(frozen=True)
class _TelegramPlugin:
    name: str
    capabilities: ChannelCapabilities
    router: APIRouter | None


_plugin = _TelegramPlugin(
    name='telegram',
    capabilities=ChannelCapabilities(
        markdown=True,
        rich_ui=True,
        streaming=True,
        progress_updates=True,
        attachments=True,
    ),
    router=router,
)

register_channel(_plugin)

__all__ = ['router']
