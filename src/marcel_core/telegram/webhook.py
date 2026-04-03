"""Telegram webhook endpoint.

Receives updates from the Telegram Bot API and routes them through the
Marcel agent loop. Responds to each message after streaming completes.

Commands:
    /start  — show chat ID for account linking
    /new    — start a fresh conversation

Webhook URL: POST /telegram/webhook
"""

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response
from marcel_core.agent.events import TextMessageContent
from marcel_core.agent.sessions import session_manager
from marcel_core.telegram import bot, sessions

log = logging.getLogger(__name__)

router = APIRouter()

_ASSISTANT_TIMEOUT = 600.0


async def _reply(chat_id: int, text: str) -> None:
    """Send a plain notification message, escaping special characters."""
    await bot.send_message(chat_id, bot.escape_markdown_v2(text))


# ---------------------------------------------------------------------------
# Assistant path
# ---------------------------------------------------------------------------


async def _process_assistant_message(chat_id: int, user_slug: str, text: str) -> None:
    """Run the assistant agent for one message."""
    try:
        await _process_assistant_message_inner(chat_id, user_slug, text)
    except Exception as exc:
        log.exception('Unhandled error processing Telegram message from chat_id=%s: %s', chat_id, exc)
        try:
            await _reply(chat_id, f'Sorry, an unexpected error occurred: {exc}')
        except Exception:
            log.exception('Also failed to send error reply to chat_id=%s', chat_id)


async def _process_assistant_message_inner(chat_id: int, user_slug: str, text: str) -> None:
    conversation_id = sessions.get_conversation_id(chat_id)

    if conversation_id is None:
        async with storage.get_lock(user_slug):
            conversation_id = storage.new_conversation(user_slug, 'telegram')
        sessions.set_conversation_id(chat_id, conversation_id)

    response_parts: list[str] = []
    try:

        async def _collect() -> None:
            async for event in stream_response(user_slug, 'telegram', text, conversation_id):
                if isinstance(event, TextMessageContent):
                    response_parts.append(event.text)

        await asyncio.wait_for(_collect(), timeout=_ASSISTANT_TIMEOUT)
    except asyncio.TimeoutError:
        await _reply(chat_id, 'Sorry, that took too long and I had to give up. Please try again.')
        return
    except Exception as exc:
        await _reply(chat_id, f'Sorry, something went wrong: {exc}')
        return

    full_response = ''.join(response_parts)

    if not full_response.strip():
        await _reply(
            chat_id,
            'Sorry, I received your message but produced an empty response. Please try again or rephrase your question.',
        )
        return

    async with storage.get_lock(user_slug):
        storage.append_turn(user_slug, conversation_id, 'user', text)
        storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

    asyncio.create_task(extract_and_save_memories(user_slug, text, full_response, conversation_id))

    try:
        markup = bot.rich_content_markup() if bot.has_rich_content(full_response) else None
        await bot.send_message(chat_id, full_response, reply_markup=markup)
    except Exception as exc:
        await _reply(chat_id, f'I have a response but failed to send it: {exc}')


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post('/telegram/webhook')
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Receive an incoming update from the Telegram Bot API.

    Validates the optional webhook secret header, parses the update, and
    dispatches message handling as a background task so Telegram's 5-second
    timeout is not exceeded.

    Returns:
        ``{"status": "ok"}`` for handled updates, ``{"status": "ignored"}``
        for updates without an actionable message.
    """
    secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
    if not secret:
        raise HTTPException(status_code=503, detail='TELEGRAM_WEBHOOK_SECRET is not configured')
    token_header = request.headers.get('x-telegram-bot-api-secret-token', '')
    if token_header != secret:
        raise HTTPException(status_code=403, detail='Invalid webhook secret')

    update: dict[str, Any] = await request.json()

    message: dict[str, Any] | None = update.get('message') or update.get('edited_message')
    if not message:
        return {'status': 'ignored'}

    chat_id: int = message['chat']['id']
    text: str = message.get('text', '').strip()

    if not text:
        return {'status': 'ignored'}

    # --- /start: show chat ID for account linking ---
    if text == '/start':
        escaped_id = bot.escape_markdown_v2(str(chat_id))
        await bot.send_message(
            chat_id,
            f'Hi\\! Your Telegram chat ID is `{escaped_id}`\\.\n\n'
            f'Share this with your Marcel admin to link your account\\.',
        )
        return {'status': 'ok'}

    user_slug = sessions.get_user_slug(chat_id)
    if user_slug is None:
        escaped_id = bot.escape_markdown_v2(str(chat_id))
        await bot.send_message(
            chat_id,
            f'This chat is not linked to a Marcel user\\.\n\n'
            f'Your chat ID is `{escaped_id}`\\. Ask your admin to add it to `TELEGRAM_USER_MAP`\\.',
        )
        return {'status': 'ok'}

    # --- /new: reset session, start fresh ---
    if text == '/new':
        await session_manager.reset_user(user_slug)
        sessions.reset_session(chat_id)
        await _reply(chat_id, 'Fresh start! Previous conversation cleared.')
        return {'status': 'ok'}

    # --- Auto-new on inactivity ---
    if sessions.should_auto_new(chat_id):
        await session_manager.reset_user(user_slug)
        sessions.reset_session(chat_id)
        log.info('Auto-new conversation for chat_id=%s (inactivity)', chat_id)

    # Update last-message timestamp
    sessions.touch_last_message(chat_id)

    # --- Dispatch to assistant ---
    await _reply(chat_id, 'Got it, working on it...')
    log.info('Dispatching message from chat_id=%s user=%s: %r', chat_id, user_slug, text[:80])
    asyncio.create_task(_process_assistant_message(chat_id, user_slug, text))

    return {'status': 'ok'}
