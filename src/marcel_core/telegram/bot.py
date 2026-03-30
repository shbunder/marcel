"""Telegram Bot API client.

Provides a thin async wrapper around the Telegram sendMessage endpoint.
Tries MarkdownV2 parse mode first; falls back to plain text if Telegram
rejects the formatting.
"""

import os
import re

import httpx

_API_BASE = 'https://api.telegram.org'

# Characters that must be escaped in Telegram MarkdownV2
_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def escape_markdown_v2(text: str) -> str:
    """Escape a plain-text string for safe use inside a MarkdownV2 message.

    Args:
        text: Raw text that should appear literally (not interpreted as markup).

    Returns:
        Text with all MarkdownV2 special characters backslash-escaped.
    """
    return _ESCAPE_RE.sub(r'\\\1', text)


def _token() -> str:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN is not set in the environment')
    return token


async def send_message(chat_id: int | str, text: str) -> None:
    """Send a text message to a Telegram chat.

    Attempts delivery with MarkdownV2 parse mode. If Telegram rejects the
    request (e.g. malformed markup), retries with plain text so the user
    always receives a response.

    Args:
        chat_id: The Telegram chat or user ID to send to.
        text: Message text; may contain Telegram MarkdownV2 markup.
    """
    token = _token()
    url = f'{_API_BASE}/bot{token}/sendMessage'

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'MarkdownV2',
            },
        )
        if resp.is_success:
            return

        # MarkdownV2 rejected — retry as plain text so the user isn't left hanging
        plain_resp = await client.post(url, json={'chat_id': chat_id, 'text': text})
        if not plain_resp.is_success:
            raise RuntimeError(f'Telegram sendMessage failed: {plain_resp.status_code} {plain_resp.text}')


async def set_webhook(url: str, *, secret: str = '') -> dict:
    """Register a webhook URL with the Telegram Bot API.

    Args:
        url: The public HTTPS URL Telegram should call for updates.
        secret: Optional secret token sent as ``X-Telegram-Bot-Api-Secret-Token``.

    Returns:
        The parsed JSON response from Telegram.
    """
    token = _token()
    payload: dict = {'url': url}
    if secret:
        payload['secret_token'] = secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(f'{_API_BASE}/bot{token}/setWebhook', json=payload)
        resp.raise_for_status()
        return resp.json()


async def delete_webhook() -> dict:
    """Deregister the current webhook (switches bot to polling mode).

    Returns:
        The parsed JSON response from Telegram.
    """
    token = _token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f'{_API_BASE}/bot{token}/deleteWebhook')
        resp.raise_for_status()
        return resp.json()
