"""WebSocket chat endpoint.

Protocol (defined in docs/architecture.md):

Client → server:
    {"text": "...", "user": "shaun", "conversation": null | "2026-03-26T14-32", "channel": "cli"}

Server → client (in order):
    {"type": "started", "conversation": "2026-03-26T14-32"}   # only when conversation was null
    {"type": "token", "text": "..."}                          # streamed, one or many
    {"type": "done"}                                           # end of turn
    {"type": "error", "message": "..."}                       # on failure (replaces done)
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marcel_core import storage
from marcel_core.agent import extract_and_save_memories, stream_response

router = APIRouter()


@router.websocket('/ws/chat')
async def chat(websocket: WebSocket) -> None:
    """Streaming chat endpoint. One WebSocket connection, many turns."""
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            user_text: str = data.get('text', '').strip()
            user_slug: str = data.get('user', 'shaun')
            conversation_id: str | None = data.get('conversation')
            channel: str = data.get('channel', 'cli')

            if not user_text:
                await websocket.send_text(json.dumps({'type': 'error', 'message': 'Empty message'}))
                continue

            # Start a new conversation if none was provided
            if conversation_id is None:
                async with storage.get_lock(user_slug):
                    conversation_id = storage.new_conversation(user_slug, channel)
                await websocket.send_text(
                    json.dumps({'type': 'started', 'conversation': conversation_id})
                )

            # Stream the agent response token by token
            response_parts: list[str] = []
            try:
                async for token in stream_response(user_slug, channel, user_text, conversation_id):
                    response_parts.append(token)
                    await websocket.send_text(json.dumps({'type': 'token', 'text': token}))
            except Exception as exc:
                await websocket.send_text(json.dumps({'type': 'error', 'message': str(exc)}))
                continue

            full_response = ''.join(response_parts)

            # Persist the turn (lock only for the file writes, not during streaming)
            async with storage.get_lock(user_slug):
                storage.append_turn(user_slug, conversation_id, 'user', user_text)
                storage.append_turn(user_slug, conversation_id, 'assistant', full_response)

            # Fire-and-forget memory extraction
            asyncio.create_task(
                extract_and_save_memories(user_slug, user_text, full_response, conversation_id)
            )

            await websocket.send_text(json.dumps({'type': 'done'}))

    except WebSocketDisconnect:
        pass
