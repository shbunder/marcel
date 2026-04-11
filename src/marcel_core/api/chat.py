"""WebSocket chat endpoint — pydantic-ai harness.

Protocol (defined in docs/architecture.md):

Client → server (first message must authenticate):
    {"token": "...", "text": "...", "user": "alice", "conversation": null | "2026-03-26T14-32", "channel": "cli"}

Server → client (in order):
    {"type": "started", "conversation": "2026-03-26T14-32"}           # only when conversation was null
    {"type": "text_message_start"}                                     # text block opened
    {"type": "token", "text": "..."}                                   # streamed, one or many
    {"type": "text_message_end"}                                       # text block closed
    {"type": "tool_call_start", "tool_call_id": "...", "tool_name": "..."}
    {"type": "tool_call_end", "tool_call_id": "..."}
    {"type": "done", "cost_usd": 0.012}                               # end of turn
    {"type": "error", "message": "..."}                                # on failure (replaces done)
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.channels.adapter import dispatch_event
from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug
from marcel_core.channels.websocket import WebSocketAdapter
from marcel_core.config import settings
from marcel_core.harness.runner import (
    TextDelta,
    stream_turn,
)
from marcel_core.memory import extract_and_save_memories
from marcel_core.memory.conversation import ensure_channel

log = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_USER = settings.marcel_default_user


@router.websocket('/ws/chat')
async def chat(websocket: WebSocket) -> None:
    """Streaming chat endpoint. One WebSocket connection, many turns."""
    await websocket.accept()
    adapter = WebSocketAdapter(websocket)
    authenticated = False
    forced_user_slug: str | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            # Authenticate on first message
            if not authenticated:
                init_data: str = data.get('initData', '')
                token: str = data.get('token', '')

                if init_data:
                    tg_user = verify_telegram_init_data(init_data)
                    if tg_user is None:
                        await adapter.send_error('Invalid Telegram credentials')
                        await websocket.close(code=4001, reason='Unauthorized')
                        return
                    slug = get_telegram_user_slug(tg_user['id'])
                    if slug is None:
                        await adapter.send_error('Telegram user not linked to a Marcel account')
                        await websocket.close(code=4001, reason='Unauthorized')
                        return
                    forced_user_slug = slug
                elif not verify_api_token(token):
                    await adapter.send_error('Invalid or missing API token')
                    await websocket.close(code=4001, reason='Unauthorized')
                    return
                authenticated = True

            user_text: str = data.get('text', '').strip()
            user_slug: str = forced_user_slug or data.get('user', '') or _DEFAULT_USER
            if not user_slug:
                await adapter.send_error('No user specified and MARCEL_DEFAULT_USER is not set')
                continue
            if not valid_user_slug(user_slug):
                await adapter.send_error('Invalid user slug — only a-z, 0-9, _ and - are allowed')
                continue
            conversation_id: str | None = data.get('conversation')
            channel: str = data.get('channel', 'cli')
            model: str | None = data.get('model') or None
            cwd: str | None = data.get('cwd') or None

            if not user_text:
                await adapter.send_error('Empty message')
                continue

            # Ensure continuous conversation exists for this channel
            if conversation_id is None:
                ensure_channel(user_slug, channel)
                conversation_id = f'{channel}-default'
                await adapter.send_conversation_started(conversation_id)

            # Stream the agent response
            response_parts: list[str] = []
            text_started = False

            try:
                async for event in stream_turn(user_slug, channel, user_text, conversation_id, model=model, cwd=cwd):
                    if isinstance(event, TextDelta):
                        response_parts.append(event.text)
                    text_started = await dispatch_event(adapter, event, text_started=text_started)

            except Exception as exc:
                log.exception('chat: turn execution failed: %s', type(exc).__name__)
                try:
                    await adapter.send_error(str(exc))
                except Exception:
                    pass
                continue

            full_response = ''.join(response_parts)

            # Fire-and-forget memory extraction
            asyncio.create_task(extract_and_save_memories(user_slug, user_text, full_response, conversation_id))

    except WebSocketDisconnect:
        log.info('chat: websocket disconnected')
    except BaseException as exc:
        log.exception('chat: unexpected error (%s) — websocket will close', type(exc).__name__)
