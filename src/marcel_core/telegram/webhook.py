"""Telegram webhook endpoint.

Receives updates from the Telegram Bot API and routes them through the
Marcel agent loop. Responds to each message after streaming completes.

Commands:
    /start  — show chat ID for account linking
    /code   — enter coder mode (self-modification via Claude Code)
    /done   — exit coder mode early
    /new    — start a fresh conversation (resets mode and conversation)

Webhook URL: POST /telegram/webhook
"""

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response
from marcel_core.agent.coder import CoderResult, run_coder_task
from marcel_core.telegram import bot, sessions

log = logging.getLogger(__name__)

router = APIRouter()

# Timeout for coder tasks (seconds) — much longer than assistant (120s).
_CODER_TIMEOUT = 600.0
_ASSISTANT_TIMEOUT = 120.0

# Running coder tasks keyed by chat_id — allows /done to cancel mid-execution.
_running_coder_tasks: dict[int, asyncio.Task[None]] = {}


async def _reply(chat_id: int, text: str) -> None:
    """Send a plain notification message, escaping special characters."""
    await bot.send_message(chat_id, bot.escape_markdown_v2(text))


# ---------------------------------------------------------------------------
# Assistant path (existing)
# ---------------------------------------------------------------------------


async def _process_assistant_message(chat_id: int, user_slug: str, text: str) -> None:
    """Run the standard assistant agent for one message."""
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
            async for token in stream_response(user_slug, 'telegram', text, conversation_id):
                response_parts.append(token)

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
        await bot.send_message(chat_id, full_response)
    except Exception as exc:
        await _reply(chat_id, f'I have a response but failed to send it: {exc}')


# ---------------------------------------------------------------------------
# Coder path
# ---------------------------------------------------------------------------


async def _process_coder_message(chat_id: int, text: str) -> None:
    """Run a coder task (or continue one) and send the result to Telegram."""
    try:
        await _process_coder_message_inner(chat_id, text)
    except asyncio.CancelledError:
        log.info('Coder task cancelled for chat_id=%s', chat_id)
        sessions.exit_coder_mode(chat_id)
        await _reply(chat_id, '🤖 Coder task cancelled. Back to assistant mode.')
    except Exception as exc:
        log.exception('Unhandled error in coder task for chat_id=%s: %s', chat_id, exc)
        sessions.exit_coder_mode(chat_id)
        try:
            await _reply(chat_id, f'🤖 Coder task failed: {exc}')
        except Exception:
            log.exception('Also failed to send coder error reply to chat_id=%s', chat_id)
    finally:
        _running_coder_tasks.pop(chat_id, None)


async def _process_coder_message_inner(chat_id: int, text: str) -> None:
    resume_id = sessions.get_coder_session_id(chat_id)

    async def _on_progress(description: str) -> None:
        await _reply(chat_id, description)

    try:
        result: CoderResult = await asyncio.wait_for(
            run_coder_task(prompt=text, resume_session_id=resume_id, on_progress=_on_progress),
            timeout=_CODER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        sessions.exit_coder_mode(chat_id)
        await _reply(chat_id, '🤖 Coder task timed out after 10 minutes. Back to assistant mode.')
        return
    except RuntimeError as exc:
        # Another coder task is already running
        await _reply(chat_id, str(exc))
        return

    # Auto-exit coder mode after each task completes.
    sessions.exit_coder_mode(chat_id)

    if not result.response.strip():
        await _reply(chat_id, '🤖 Coder task completed but produced no output. Back to assistant mode.')
        return

    try:
        await bot.send_message(chat_id, result.response)
    except Exception as exc:
        await _reply(chat_id, f'Coder task finished but I failed to send the result: {exc}')


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
        sessions.reset_session(chat_id)
        await _reply(chat_id, '🤖 Fresh start! Previous conversation and coder mode cleared.')
        return {'status': 'ok'}

    # --- /done: cancel running coder task and/or exit coder mode ---
    if text == '/done':
        task = _running_coder_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            await _reply(chat_id, '👾 Cancelling coder task...')
        elif sessions.get_mode(chat_id) == 'coder':
            sessions.exit_coder_mode(chat_id)
            await _reply(chat_id, '🤖 Coder mode exited. Back to assistant mode.')
        else:
            await _reply(chat_id, 'Not in coder mode — nothing to exit.')
        return {'status': 'ok'}

    # --- Auto-new on inactivity ---
    if sessions.should_auto_new(chat_id):
        sessions.reset_session(chat_id)
        log.info('Auto-new conversation for chat_id=%s (inactivity)', chat_id)

    # Update last-message timestamp
    sessions.touch_last_message(chat_id)

    # --- /code: enter coder mode ---
    if text.startswith('/code'):
        coder_prompt = text.removeprefix('/code').strip()
        if not coder_prompt:
            await _reply(chat_id, 'Usage: /code <describe the change you want>')
            return {'status': 'ok'}

        sessions.enter_coder_mode(chat_id)
        await _reply(chat_id, '👾 Entering coder mode. This may take a while... (send /done to cancel)')
        log.info('Coder mode started for chat_id=%s: %r', chat_id, coder_prompt[:80])
        _running_coder_tasks[chat_id] = asyncio.create_task(_process_coder_message(chat_id, coder_prompt))
        return {'status': 'ok'}

    # --- Route based on current mode ---
    mode = sessions.get_mode(chat_id)

    if mode == 'coder':
        await _reply(chat_id, '👾 Continuing coder session...')
        log.info('Coder follow-up from chat_id=%s: %r', chat_id, text[:80])
        _running_coder_tasks[chat_id] = asyncio.create_task(_process_coder_message(chat_id, text))
    else:
        await _reply(chat_id, '🤖 Got it, working on it...')
        log.info('Dispatching message from chat_id=%s user=%s: %r', chat_id, user_slug, text[:80])
        asyncio.create_task(_process_assistant_message(chat_id, user_slug, text))

    return {'status': 'ok'}
