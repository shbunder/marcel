"""Telegram Bot API client.

Provides a thin async wrapper around the Telegram sendMessage endpoint.
Tries MarkdownV2 parse mode first; falls back to plain text if Telegram
rejects the formatting.
"""

import os
import re

import httpx

_API_BASE = 'https://api.telegram.org'

# Patterns that indicate the response contains rich content worth viewing in
# the Mini App (calendar events, task lists, markdown tables).
_RICH_TABLE_RE = re.compile(r'\|.+\|.+\|')
_RICH_TASKLIST_RE = re.compile(r'^- \[[ xX]\] ', re.MULTILINE)
# Calendar-style: lines with time ranges (10:00–12:00) or date headers (Apr 3, Monday Apr 6)
_RICH_CALENDAR_RE = re.compile(r'\d{1,2}:\d{2}[–\-]\d{1,2}:\d{2}|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}', re.IGNORECASE)

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


async def send_message(chat_id: int | str, text: str, *, reply_markup: dict | None = None) -> None:
    """Send a text message to a Telegram chat.

    Attempts delivery with MarkdownV2 parse mode. If Telegram rejects the
    request (e.g. malformed markup), retries with plain text so the user
    always receives a response.

    Args:
        chat_id: The Telegram chat or user ID to send to.
        text: Message text; may contain Telegram MarkdownV2 markup.
        reply_markup: Optional inline keyboard markup (passed through as-is).
    """
    token = _token()
    url = f'{_API_BASE}/bot{token}/sendMessage'

    payload: dict = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'MarkdownV2',
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload)
        if resp.is_success:
            return

        # MarkdownV2 rejected — retry as plain text so the user isn't left hanging
        plain_payload: dict = {'chat_id': chat_id, 'text': text}
        if reply_markup:
            plain_payload['reply_markup'] = reply_markup
        plain_resp = await client.post(url, json=plain_payload)
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


# ---------------------------------------------------------------------------
# Mini App helpers
# ---------------------------------------------------------------------------


def _public_url() -> str | None:
    """Return the configured public URL, or None if not set."""
    return os.environ.get('MARCEL_PUBLIC_URL') or None


def has_rich_content(text: str) -> bool:
    """Return True if *text* contains patterns that render better in the Mini App."""
    return bool(
        _RICH_TABLE_RE.search(text) or _RICH_TASKLIST_RE.search(text) or _RICH_CALENDAR_RE.search(text)
    )


def _detect_content_type(text: str) -> str:
    """Return a label for the primary rich content type in *text*."""
    if _RICH_CALENDAR_RE.search(text) or _RICH_TABLE_RE.search(text):
        return 'calendar'
    return 'checklist'


_BUTTON_LABELS = {
    'calendar': '📅 Show events',
    'checklist': '☑️ Show checklist',
}


def rich_content_markup(conversation_id: str | None = None) -> dict | None:
    """Return an InlineKeyboardMarkup that opens the Mini App, or None."""
    url = _public_url()
    if not url:
        return None
    app_url = url
    if conversation_id:
        app_url = f'{url}?conversation={conversation_id}'
    return {
        'inline_keyboard': [[{'text': '✨ View in app', 'web_app': {'url': app_url}}]],
    }


async def set_menu_button() -> dict | None:
    """Set the bot menu button to open the Mini App.

    Requires ``MARCEL_PUBLIC_URL`` to be set. Returns ``None`` if not
    configured, otherwise the Telegram API response.
    """
    url = _public_url()
    if not url:
        return None
    token = _token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f'{_API_BASE}/bot{token}/setChatMenuButton',
            json={'menu_button': {'type': 'web_app', 'text': 'Open Marcel', 'web_app': {'url': url}}},
        )
        resp.raise_for_status()
        return resp.json()
