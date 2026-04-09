"""WebSocket chat endpoint v2 — uses the new pydantic-ai harness.

This is the v2 endpoint that runs alongside the existing /ws/chat during migration.
It uses the new harness with JSONL history, paste store, and pydantic-ai tools.

Enable with: MARCEL_USE_V2=true
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories
from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.channels.websocket import WebSocketAdapter
from marcel_core.harness.runner import MarcelEvent, RunFinished, RunStarted, TextDelta, ToolCallCompleted, ToolCallStarted
from marcel_core.harness.runner import stream_turn
from marcel_core.memory.compactor import check_and_compact
from marcel_core.memory.history import append_message, HistoryMessage
from marcel_core.telegram.sessions import get_user_slug as get_telegram_user_slug
from datetime import datetime, timezone

log = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_USER = os.environ.get('MARCEL_DEFAULT_USER', '')


@router.websocket('/v2/chat')
async def chat_v2(websocket: WebSocket) -> None:
    """Streaming chat endpoint v2 using new pydantic-ai harness.

    Protocol is compatible with /ws/chat for drop-in replacement.
    """
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
            channel: str = data.get('channel', 'websocket')
            model: str | None = data.get('model') or None

            if not user_text:
                await adapter.send_error('Empty message')
                continue

            # Start a new conversation if none was provided
            if conversation_id is None:
                async with storage.get_lock(user_slug):
                    conversation_id = storage.new_conversation(user_slug, channel)
                await adapter.send_conversation_started(conversation_id)

            # Check if compaction is needed before processing
            asyncio.create_task(check_and_compact(user_slug, conversation_id))

            # Stream the agent response using new harness
            response_parts: list[str] = []
            run_finished: RunFinished | None = None
            text_started = False

            try:
                await adapter.send_text_message_start()
                text_started = True

                async for event in stream_turn(user_slug, channel, user_text, conversation_id, model=model):
                    if isinstance(event, RunStarted):
                        # Turn started (already sent conversation_started if needed)
                        pass
                    elif isinstance(event, TextDelta):
                        response_parts.append(event.text)
                        await adapter.send_text_delta(event.text)
                    elif isinstance(event, ToolCallStarted):
                        # Close text message block if open
                        if text_started:
                            await adapter.send_text_message_end()
                            text_started = False
                        await adapter.send_tool_call_started(event.tool_call_id, event.tool_name)
                    elif isinstance(event, ToolCallCompleted):
                        await adapter.send_tool_call_completed(
                            event.tool_call_id, event.tool_name, event.result, event.is_error
                        )
                    elif isinstance(event, RunFinished):
                        run_finished = event

                if text_started:
                    await adapter.send_text_message_end()

            except Exception as exc:
                log.exception('[chat_v2] Turn execution failed')
                await adapter.send_error(str(exc))
                continue

            full_response = ''.join(response_parts)

            # Persist to old Markdown format (dual-write during migration)
            # The new harness already wrote to JSONL history
            async with storage.get_lock(user_slug):
                storage.append_turn(user_slug, conversation_id, 'user', user_text)
                storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

            # Fire-and-forget memory extraction
            asyncio.create_task(extract_and_save_memories(user_slug, user_text, full_response, conversation_id))

            # Send done message
            cost = run_finished.total_cost_usd if run_finished else None
            is_error = run_finished.is_error if run_finished else False
            await adapter.send_run_finished(cost, is_error)

    except WebSocketDisconnect:
        log.info('[chat_v2] WebSocket disconnected')
