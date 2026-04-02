"""WebSocket chat endpoint.

Protocol (defined in docs/architecture.md):

Client → server (first message must authenticate):
    {"token": "...", "text": "...", "user": "alice", "conversation": null | "2026-03-26T14-32", "channel": "cli"}

Server → client (in order):
    {"type": "started", "conversation": "2026-03-26T14-32"}           # only when conversation was null
    {"type": "text_message_start"}                                     # AG-UI: text block opened
    {"type": "token", "text": "..."}                                   # streamed, one or many
    {"type": "text_message_end"}                                       # AG-UI: text block closed
    {"type": "tool_call_start", "tool_call_id": "...", "tool_name": "..."}  # AG-UI
    {"type": "tool_call_end", "tool_call_id": "..."}                        # AG-UI
    {"type": "tool_call_result", "tool_call_id": "...", ...}                # AG-UI
    {"type": "done", "cost_usd": 0.012, "turns": 3}                   # end of turn
    {"type": "error", "message": "..."}                                # on failure (replaces done)
"""

import asyncio
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response
from marcel_core.agent.events import (
    RunFinished,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
)
from marcel_core.auth import valid_user_slug, verify_api_token

router = APIRouter()

_DEFAULT_USER = os.environ.get('MARCEL_DEFAULT_USER', '')


@router.websocket('/ws/chat')
async def chat(websocket: WebSocket) -> None:
    """Streaming chat endpoint. One WebSocket connection, many turns."""
    await websocket.accept()
    authenticated = False
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            # Authenticate on first message (or every message if token is present)
            token: str = data.get('token', '')
            if not authenticated:
                if not verify_api_token(token):
                    await websocket.send_text(json.dumps({'type': 'error', 'message': 'Invalid or missing API token'}))
                    await websocket.close(code=4001, reason='Unauthorized')
                    return
                authenticated = True

            user_text: str = data.get('text', '').strip()
            user_slug: str = data.get('user', '') or _DEFAULT_USER
            if not user_slug:
                await websocket.send_text(
                    json.dumps({'type': 'error', 'message': 'No user specified and MARCEL_DEFAULT_USER is not set'})
                )
                continue
            if not valid_user_slug(user_slug):
                await websocket.send_text(
                    json.dumps({'type': 'error', 'message': 'Invalid user slug — only a-z, 0-9, _ and - are allowed'})
                )
                continue
            conversation_id: str | None = data.get('conversation')
            channel: str = data.get('channel', 'cli')
            model: str | None = data.get('model') or None

            if not user_text:
                await websocket.send_text(json.dumps({'type': 'error', 'message': 'Empty message'}))
                continue

            # Start a new conversation if none was provided
            if conversation_id is None:
                async with storage.get_lock(user_slug):
                    conversation_id = storage.new_conversation(user_slug, channel)
                await websocket.send_text(json.dumps({'type': 'started', 'conversation': conversation_id}))

            # Stream the agent response as AG-UI events
            response_parts: list[str] = []
            run_finished = RunFinished()
            try:
                async for event in stream_response(user_slug, channel, user_text, conversation_id, model=model):
                    if isinstance(event, TextMessageContent):
                        response_parts.append(event.text)
                        await websocket.send_text(json.dumps({'type': 'token', 'text': event.text}))
                    elif isinstance(event, RunFinished):
                        run_finished = event
                    elif isinstance(
                        event, (TextMessageStart, TextMessageEnd, ToolCallStart, ToolCallEnd, ToolCallResult)
                    ):
                        await websocket.send_text(json.dumps(event.to_dict()))
            except Exception as exc:
                await websocket.send_text(json.dumps({'type': 'error', 'message': str(exc)}))
                continue

            full_response = ''.join(response_parts)

            # Persist the turn as an audit log (lock only for file writes)
            async with storage.get_lock(user_slug):
                storage.append_turn(user_slug, conversation_id, 'user', user_text)
                storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

            # Fire-and-forget memory extraction
            asyncio.create_task(extract_and_save_memories(user_slug, user_text, full_response, conversation_id))

            done_msg: dict[str, object] = {'type': 'done'}
            if run_finished.total_cost_usd is not None:
                done_msg['cost_usd'] = run_finished.total_cost_usd
            if run_finished.num_turns:
                done_msg['turns'] = run_finished.num_turns
            await websocket.send_text(json.dumps(done_msg))

    except WebSocketDisconnect:
        pass
