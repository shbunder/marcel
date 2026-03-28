import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket('/ws/chat')
async def chat(websocket: WebSocket) -> None:
    """WebSocket chat endpoint. Echoes messages back for now — agent wired in ISSUE-003."""
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            text = data.get('text', '')
            await websocket.send_text(json.dumps({'type': 'token', 'text': f'echo: {text}'}))
            await websocket.send_text(json.dumps({'type': 'done'}))
    except WebSocketDisconnect:
        pass
