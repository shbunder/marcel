"""WebSocket chat client for the Marcel CLI.

Handles connection lifecycle, message streaming, and conversation state.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from enum import Enum, auto

import websockets
from websockets.asyncio.client import ClientConnection


class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()


class ChatClient:
    """Async WebSocket client for the Marcel chat endpoint.

    Args:
        ws_url: Full WebSocket URL (e.g. ``ws://localhost:8000/ws/chat``).
        user: User slug to include in each message.
        token: Auth token (sent as query param; not validated in Phase 1).
    """

    def __init__(self, ws_url: str, user: str, token: str = '') -> None:
        self._ws_url = ws_url
        self._user = user
        self._token = token
        self._conn: ClientConnection | None = None
        self._conversation_id: str | None = None
        self.state = ConnectionState.DISCONNECTED

    async def connect(self) -> None:
        """Open the WebSocket connection."""
        self.state = ConnectionState.CONNECTING
        url = self._ws_url
        if self._token:
            url = f'{url}?token={self._token}'
        self._conn = await websockets.connect(url)
        self.state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self.state = ConnectionState.DISCONNECTED

    async def send(self, text: str) -> AsyncIterator[str]:
        """Send a message and yield response tokens as they arrive.

        Yields:
            Text tokens from the server.  The caller receives each token
            as soon as it arrives so the UI can stream them.

        Raises:
            RuntimeError: If not connected.
        """
        if self._conn is None:
            raise RuntimeError('Not connected')

        payload = {
            'text': text,
            'user': self._user,
            'conversation': self._conversation_id,
        }
        await self._conn.send(json.dumps(payload))
        return self._receive_tokens()

    async def _receive_tokens(self) -> AsyncIterator[str]:
        assert self._conn is not None
        async for raw in self._conn:
            msg = json.loads(raw)
            msg_type = msg.get('type')
            if msg_type == 'started':
                self._conversation_id = msg.get('conversation')
            elif msg_type == 'token':
                yield msg.get('text', '')
            elif msg_type == 'done':
                break
            elif msg_type == 'error':
                yield f"\n[Error: {msg.get('message', 'unknown error')}]"
                break
