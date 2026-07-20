"""Guided Telegram webhook setup (FEAT-260707-6130cd, STORY-533eb0, FR2).

Registers the webhook via the Bot API and verifies it with a round-trip —
no hand-written curl. Kernel-self-contained: talks to the Bot API directly
over https rather than depending on the telegram zoo habitat, so the target
works before any habitat is wired.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_API = 'https://api.telegram.org'
_WEBHOOK_PATH = '/telegram/webhook'
_TIMEOUT = 20.0


class TelegramSetupError(RuntimeError):
    """Setup could not complete — message is admin-readable."""


async def setup_webhook(public_url: str) -> str:
    """Register the Telegram webhook at ``<public_url>/telegram/webhook`` and verify it.

    Reads the bot token and webhook secret from settings (``.env``); the
    secret is sent as ``secret_token`` so the webhook endpoint can reject
    forged updates. Returns a human-readable confirmation, or raises
    :class:`TelegramSetupError` naming the fix.
    """
    import httpx

    from marcel_core.config import settings

    token = settings.telegram_bot_token
    if not token:
        raise TelegramSetupError('TELEGRAM_BOT_TOKEN is not set — add it to .env.local (from @BotFather) and retry.')
    if not public_url.startswith('https://'):
        raise TelegramSetupError(f'Public URL must be https (Telegram requires it), got {public_url!r}.')

    webhook_url = f'{public_url.rstrip("/")}{_WEBHOOK_PATH}'
    secret = settings.telegram_webhook_secret

    async with httpx.AsyncClient(base_url=f'{_API}/bot{token}', timeout=_TIMEOUT) as client:
        payload: dict[str, object] = {'url': webhook_url, 'drop_pending_updates': True}
        if secret:
            payload['secret_token'] = secret
        set_resp = await _call(client, 'setWebhook', payload)
        if not set_resp.get('result', False):
            raise TelegramSetupError(f'setWebhook did not succeed: {set_resp.get("description", "unknown")}.')

        info = await _call(client, 'getWebhookInfo', {})
        registered = (info.get('result') or {}).get('url', '')
        if registered != webhook_url:
            raise TelegramSetupError(
                f'Webhook verification failed — Telegram reports {registered!r}, expected {webhook_url!r}.'
            )
        pending = (info.get('result') or {}).get('pending_update_count', 0)

    secret_note = 'with a secret token' if secret else 'WITHOUT a secret token (set TELEGRAM_WEBHOOK_SECRET to harden)'
    return (
        f'Telegram webhook registered and verified at {webhook_url} ({secret_note}). '
        f'Pending updates: {pending}. Message your bot to test.'
    )


async def _call(client, method: str, payload: dict) -> dict:
    import httpx

    try:
        resp = await client.post(f'/{method}', json=payload)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        # 401 = bad token, 404 = wrong bot id — surface the Bot API's reason.
        detail = ''
        try:
            detail = exc.response.json().get('description', '')
        except Exception:
            detail = exc.response.text[:200]
        raise TelegramSetupError(f'Bot API {method} failed ({exc.response.status_code}): {detail}') from exc
    except httpx.HTTPError as exc:
        raise TelegramSetupError(f'Could not reach the Bot API for {method}: {exc}') from exc
