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
import math
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response
from marcel_core.agent.events import TextMessageContent
from marcel_core.agent.sessions import session_manager
from marcel_core.telegram import bot, sessions
from marcel_core.telegram.formatting import (
    DAYS_PER_PAGE,
    calendar_nav_markup,
    escape_html,
    format_calendar_page,
    markdown_to_telegram_html,
    parse_day_groups,
    web_app_url_for,
)

log = logging.getLogger(__name__)

router = APIRouter()

_ASSISTANT_TIMEOUT = 600.0

# Delay before showing "Working on it..." acknowledgment (seconds).
_ACK_DELAY = 10.0


async def _reply(chat_id: int, text: str) -> int | None:
    """Send a plain notification message, escaping for HTML."""
    return await bot.send_message(chat_id, escape_html(text))


# ---------------------------------------------------------------------------
# Assistant path
# ---------------------------------------------------------------------------


async def _process_with_delayed_ack(chat_id: int, user_slug: str, text: str) -> None:
    """Run assistant processing with a delayed acknowledgment.

    If processing takes longer than ``_ACK_DELAY`` seconds, sends a
    "Working on it..." message first, then edits it with the final response.
    """
    ack: dict[str, Any] = {'message_id': None, 'sent': False, 'cancelled': False}

    async def _send_delayed_ack() -> None:
        await asyncio.sleep(_ACK_DELAY)
        if not ack['cancelled']:
            msg_id = await bot.send_message(chat_id, escape_html('Working on it...'))
            ack['message_id'] = msg_id
            ack['sent'] = True

    ack_task = asyncio.create_task(_send_delayed_ack())

    try:
        await _process_assistant_message(chat_id, user_slug, text, ack)
    except Exception as exc:
        log.exception('Unhandled error processing Telegram message from chat_id=%s: %s', chat_id, exc)
        try:
            await _reply(chat_id, f'Sorry, an unexpected error occurred: {exc}')
        except Exception:
            log.exception('Also failed to send error reply to chat_id=%s', chat_id)
    finally:
        ack['cancelled'] = True
        ack_task.cancel()


async def _process_assistant_message(
    chat_id: int,
    user_slug: str,
    text: str,
    ack: dict[str, Any],
) -> None:
    """Run the assistant agent for one message."""
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
        # Count existing assistant turns before appending, so we know this
        # message's 0-based turn index for the "View in app" button.
        existing = storage.load_conversation(user_slug, conversation_id)
        assistant_turn = existing.count('**Marcel:** ')

        storage.append_turn(user_slug, conversation_id, 'user', text)
        storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

    asyncio.create_task(extract_and_save_memories(user_slug, text, full_response, conversation_id))

    # --- Format and send ---
    try:
        html_text, markup = _format_response(full_response, conversation_id, turn=assistant_turn)

        if ack.get('sent') and ack.get('message_id'):
            await bot.edit_message_text(chat_id, ack['message_id'], html_text, reply_markup=markup)
        else:
            await bot.send_message(chat_id, html_text, reply_markup=markup)
    except Exception as exc:
        await _reply(chat_id, f'I have a response but failed to send it: {exc}')


def _format_response(full_response: str, conversation_id: str, *, turn: int | None = None) -> tuple[str, dict | None]:
    """Convert a raw markdown response to HTML and build appropriate markup.

    Args:
        full_response: The raw markdown response text.
        conversation_id: The conversation filename stem.
        turn: 0-based assistant turn index, embedded in the "View in app" URL
            so the Mini App can fetch this specific message.

    Returns:
        A ``(html_text, reply_markup)`` tuple.
    """
    has_rich = bot.has_rich_content(full_response)

    # Try calendar pagination if the response has calendar-like content
    day_groups = parse_day_groups(full_response) if has_rich else None

    if day_groups and len(day_groups) > DAYS_PER_PAGE:
        # Multi-page calendar with navigation buttons
        html_text = format_calendar_page(day_groups, page=0)
        total_pages = math.ceil(len(day_groups) / DAYS_PER_PAGE)
        markup = calendar_nav_markup(
            conversation_id,
            page=0,
            total_pages=total_pages,
            web_app_url=web_app_url_for(conversation_id, turn=turn),
        )
    elif day_groups:
        # Single-page calendar — expandable blockquotes, no nav
        html_text = format_calendar_page(day_groups, page=0)
        markup = bot.rich_content_markup(conversation_id, turn=turn)
    else:
        # Regular message — convert markdown to HTML
        html_text = markdown_to_telegram_html(full_response)
        markup = bot.rich_content_markup(conversation_id, turn=turn) if has_rich else None

    return html_text, markup


# ---------------------------------------------------------------------------
# Callback query handler (calendar navigation)
# ---------------------------------------------------------------------------


async def _handle_callback_query(callback_query: dict[str, Any]) -> None:
    """Handle inline keyboard button presses for calendar navigation."""
    query_id = callback_query['id']
    data = callback_query.get('data', '')
    message = callback_query.get('message', {})
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')

    # Only handle calendar navigation callbacks
    if not data.startswith('cal:') or not chat_id or not message_id:
        await bot.answer_callback_query(query_id)
        return

    parts = data.split(':')
    if len(parts) != 3:
        await bot.answer_callback_query(query_id)
        return

    _, conversation_id, page_str = parts

    try:
        page = int(page_str)
    except ValueError:
        await bot.answer_callback_query(query_id, 'Invalid page')
        return

    user_slug = sessions.get_user_slug(chat_id)
    if not user_slug:
        await bot.answer_callback_query(query_id, 'Session expired')
        return

    # Load conversation and extract last assistant message
    raw = storage.load_conversation(user_slug, conversation_id)
    if not raw:
        await bot.answer_callback_query(query_id, 'Conversation not found')
        return

    from marcel_core.api.conversations import _extract_assistant_message

    assistant_text = _extract_assistant_message(raw)
    if not assistant_text:
        await bot.answer_callback_query(query_id, 'Message not found')
        return

    day_groups = parse_day_groups(assistant_text)
    if not day_groups:
        await bot.answer_callback_query(query_id, 'No calendar data')
        return

    total_pages = math.ceil(len(day_groups) / DAYS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    html_text = format_calendar_page(day_groups, page)
    markup = calendar_nav_markup(
        conversation_id,
        page,
        total_pages,
        web_app_url=web_app_url_for(conversation_id),
    )

    await bot.edit_message_text(chat_id, message_id, html_text, reply_markup=markup)
    await bot.answer_callback_query(query_id)


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

    # --- Handle callback queries (inline button presses) ---
    callback_query = update.get('callback_query')
    if callback_query:
        asyncio.create_task(_handle_callback_query(callback_query))
        return {'status': 'ok'}

    message: dict[str, Any] | None = update.get('message') or update.get('edited_message')
    if not message:
        return {'status': 'ignored'}

    chat_id: int = message['chat']['id']
    text: str = message.get('text', '').strip()

    if not text:
        return {'status': 'ignored'}

    # --- /start: show chat ID for account linking ---
    if text == '/start':
        escaped_id = escape_html(str(chat_id))
        await bot.send_message(
            chat_id,
            f'Hi! Your Telegram chat ID is <code>{escaped_id}</code>.\n\n'
            f'Share this with your Marcel admin to link your account.',
        )
        return {'status': 'ok'}

    user_slug = sessions.get_user_slug(chat_id)
    if user_slug is None:
        escaped_id = escape_html(str(chat_id))
        await bot.send_message(
            chat_id,
            f'This chat is not linked to a Marcel user.\n\n'
            f'Your chat ID is <code>{escaped_id}</code>. Ask your admin to add it to <code>TELEGRAM_USER_MAP</code>.',
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

    # --- Dispatch to assistant (with delayed ack) ---
    log.info('Dispatching message from chat_id=%s user=%s: %r', chat_id, user_slug, text[:80])
    asyncio.create_task(_process_with_delayed_ack(chat_id, user_slug, text))

    return {'status': 'ok'}
