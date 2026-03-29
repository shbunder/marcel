"""Telegram webhook endpoint.

Receives updates from the Telegram Bot API and routes them through the
Marcel agent loop. Responds to each message after streaming completes.

Webhook URL: POST /telegram/webhook
"""

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response
from marcel_core.telegram import bot, sessions

log = logging.getLogger(__name__)

router = APIRouter()


async def _reply(chat_id: int, text: str) -> None:
    """Send a plain notification message, escaping special characters."""
    await bot.send_message(chat_id, bot.escape_markdown_v2(text))


async def _process_message(chat_id: int, user_slug: str, text: str) -> None:
    """Run the agent for one incoming message and send the reply to Telegram.

    Mirrors the logic in api/chat.py: look up or create a conversation,
    stream the response, persist the turn, fire memory extraction.

    Args:
        chat_id: Telegram chat to send the reply to.
        user_slug: Marcel user slug resolved from the chat ID.
        text: The user's message text.
    """
    try:
        await _process_message_inner(chat_id, user_slug, text)
    except Exception as exc:
        log.exception('Unhandled error processing Telegram message from chat_id=%s: %s', chat_id, exc)
        try:
            await _reply(chat_id, f'Sorry, an unexpected error occurred: {exc}')
        except Exception:
            log.exception('Also failed to send error reply to chat_id=%s', chat_id)


async def _process_message_inner(chat_id: int, user_slug: str, text: str) -> None:
    conversation_id = sessions.get_conversation_id(chat_id)

    if conversation_id is None:
        async with storage.get_lock(user_slug):
            conversation_id = storage.new_conversation(user_slug, 'telegram')
        sessions.set_conversation_id(chat_id, conversation_id)

    response_parts: list[str] = []
    try:
        async def _collect() -> None:
            async for token in stream_response(user_slug, 'telegram', text, conversation_id):
                response_parts.append(token)

        await asyncio.wait_for(_collect(), timeout=120.0)
    except asyncio.TimeoutError:
        await _reply(chat_id, 'Sorry, that took too long and I had to give up. Please try again.')
        return
    except Exception as exc:
        await _reply(chat_id, f'Sorry, something went wrong: {exc}')
        return

    full_response = ''.join(response_parts)

    if not full_response.strip():
        await _reply(chat_id, 'Sorry, I received your message but produced an empty response. Please try again or rephrase your question.')
        return

    async with storage.get_lock(user_slug):
        storage.append_turn(user_slug, conversation_id, 'user', text)
        storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

    asyncio.create_task(extract_and_save_memories(user_slug, text, full_response, conversation_id))

    try:
        await bot.send_message(chat_id, full_response)
    except Exception as exc:
        await _reply(chat_id, f'I have a response but failed to send it: {exc}')


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
    if secret:
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

    # /start — tell the user their chat ID so they can share it for account linking
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

    # Acknowledge immediately so the user knows Marcel received the message
    await _reply(chat_id, 'Got it, working on it...')

    # Dispatch to background so we return 200 to Telegram immediately
    log.info('Dispatching message from chat_id=%s user=%s: %r', chat_id, user_slug, text[:80])
    asyncio.create_task(_process_message(chat_id, user_slug, text))
    return {'status': 'ok'}
