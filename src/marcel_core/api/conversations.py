"""REST endpoints for listing and fetching conversations.

Uses the JSONL session history system (v2). Legacy markdown conversations
are no longer read or written.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug
from marcel_core.memory.history import list_sessions, read_history

router = APIRouter()


class ConversationEntry(BaseModel):
    id: str
    channel: str
    first_line: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationEntry]


@router.get('/conversations', response_model=ConversationListResponse)
async def list_conversations(
    user: str = Query(..., description='User slug'),
    limit: int = Query(20, ge=1, le=100),
    authorization: str = Header(''),
) -> ConversationListResponse:
    """List recent conversations for a user, newest first."""
    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        return ConversationListResponse(conversations=[])

    if not valid_user_slug(user):
        return ConversationListResponse(conversations=[])

    sessions = list_sessions(user, limit=limit)

    entries = []
    for s in sessions:
        first_line = s.title or f'Conversation — {s.created_at.strftime("%Y-%m-%dT%H:%M")} (channel: {s.channel})'
        entries.append(
            ConversationEntry(
                id=s.session_id,
                channel=s.channel,
                first_line=first_line,
            )
        )

    return ConversationListResponse(conversations=entries)


class MessageResponse(BaseModel):
    content: str


@router.get('/api/message/{conversation_id}', response_model=MessageResponse)
async def get_last_message(
    conversation_id: str,
    initData: str = Query(''),
    authorization: str = Header(''),
    turn: int | None = Query(None, ge=0),
) -> MessageResponse:
    """Return an assistant message from a conversation.

    .. deprecated::
        Use ``GET /api/artifact/{id}`` instead. This endpoint is kept for
        backward compatibility with old "View in app" buttons.

    When *turn* is provided (0-based), returns that specific assistant
    message.  Otherwise returns the last one (backwards compatible).

    Authenticates via Telegram ``initData`` (Mini App) or Bearer token.
    """
    log.warning('Deprecated /api/message/ endpoint called for conversation=%s', conversation_id)
    user_slug: str | None = None

    # Try Telegram initData first, then Bearer token
    if initData:
        tg_user = verify_telegram_init_data(initData)
        if tg_user is None:
            raise HTTPException(status_code=401, detail='Invalid Telegram credentials')
        user_slug = get_telegram_user_slug(tg_user['id'])
        if user_slug is None:
            raise HTTPException(status_code=401, detail='Telegram user not linked')
    else:
        token = authorization.removeprefix('Bearer ').strip()
        if not verify_api_token(token):
            raise HTTPException(status_code=401, detail='Unauthorized')
        # Without initData we need a user query param — but for now this
        # endpoint is primarily for the Mini App flow, so we require initData.
        raise HTTPException(status_code=400, detail='initData required for this endpoint')

    messages = read_history(user_slug, conversation_id=conversation_id)
    assistant_msgs = [m for m in messages if m.role == 'assistant' and m.text]

    if not assistant_msgs:
        raise HTTPException(status_code=404, detail='Conversation not found')

    if turn is not None:
        if turn >= len(assistant_msgs):
            raise HTTPException(status_code=404, detail='No assistant message at that turn index')
        content = assistant_msgs[turn].text
    else:
        content = assistant_msgs[-1].text

    if not content:
        raise HTTPException(status_code=404, detail='No assistant message found')

    return MessageResponse(content=content)
