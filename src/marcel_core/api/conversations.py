"""REST endpoints for listing and fetching conversations.

Includes the history endpoint for loading conversation context on CLI startup.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug
from marcel_core.memory.conversation import load_latest_summary, read_active_segment
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


# ---------------------------------------------------------------------------
# Conversation history for CLI startup
# ---------------------------------------------------------------------------


class HistoryMessage(BaseModel):
    role: str
    text: str


class HistoryResponse(BaseModel):
    summary: str | None = None
    messages: list[HistoryMessage]


@router.get('/api/history', response_model=HistoryResponse)
async def get_conversation_history(
    user: str = Query(..., description='User slug'),
    channel: str = Query('cli', description='Channel name'),
    authorization: str = Header(''),
) -> HistoryResponse:
    """Return conversation history for the CLI to display on startup.

    Returns the latest rolling summary (if any) and all user/assistant
    text messages from the active segment. Tool calls are excluded.
    """
    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not valid_user_slug(user):
        raise HTTPException(status_code=400, detail='Invalid user slug')

    # Load latest summary
    latest = load_latest_summary(user, channel)
    summary_text = latest.summary if latest else None

    # Load active segment — only user/assistant text (skip tool, system)
    active = read_active_segment(user, channel)
    messages = [
        HistoryMessage(role=m.role, text=m.text or '') for m in active if m.role in ('user', 'assistant') and m.text
    ]

    return HistoryResponse(summary=summary_text, messages=messages)


class ForgetResponse(BaseModel):
    success: bool
    message: str


@router.post('/api/forget', response_model=ForgetResponse)
async def forget_conversation(
    user: str = Query(..., description='User slug'),
    channel: str = Query('cli', description='Channel name'),
    authorization: str = Header(''),
) -> ForgetResponse:
    """Trigger summarization of the active conversation segment.

    Equivalent to the Telegram /forget command. Seals the active segment,
    generates a summary, and opens a new active segment.
    """
    from marcel_core.memory.conversation import has_active_content
    from marcel_core.memory.summarizer import summarize_active_segment

    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not valid_user_slug(user):
        raise HTTPException(status_code=400, detail='Invalid user slug')

    if not has_active_content(user, channel):
        return ForgetResponse(success=True, message='Nothing to compress — conversation is already fresh.')

    success = await summarize_active_segment(user, channel, trigger='manual')
    if success:
        latest = load_latest_summary(user, channel)
        summary_preview = (
            latest.summary[:200] + '...' if latest and len(latest.summary) > 200 else (latest.summary if latest else '')
        )
        return ForgetResponse(
            success=True,
            message=f'Compressed. Key points preserved:\n{summary_preview}',
        )
    return ForgetResponse(success=False, message='Compression failed — please try again later.')
