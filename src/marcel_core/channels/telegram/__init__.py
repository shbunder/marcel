"""Telegram channel integration for Marcel.

Exposes a FastAPI router that receives webhook updates from the Telegram
Bot API and routes them through the Marcel agent loop.

Setup summary:
1. Create a bot via @BotFather and copy the token.
2. Set ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_USER_MAP`` in ``.env``.
3. Register the webhook: ``python -m marcel_core.channels.telegram.setup <public_url>``.

See ``docs/channels/telegram.md`` for the full setup guide.
"""

from .webhook import router

__all__ = ['router']
