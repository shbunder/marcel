"""REST endpoints for listing and fetching conversations."""

import pathlib
import re

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.storage.conversations import _conv_dir, load_conversation
from marcel_core.telegram.sessions import get_user_slug as get_telegram_user_slug

router = APIRouter()


class ConversationEntry(BaseModel):
    id: str
    channel: str
    first_line: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationEntry]


def _parse_conv_file(path: pathlib.Path) -> ConversationEntry | None:
    """Extract metadata from a conversation file's header line."""
    try:
        first_line = path.read_text(encoding='utf-8').split('\n', 1)[0]
    except OSError:
        return None

    # Header format: # Conversation — 2026-03-26T14:32 (channel: cli)
    channel = 'unknown'
    if '(channel: ' in first_line:
        channel = first_line.split('(channel: ')[1].rstrip(')')

    return ConversationEntry(
        id=path.stem,
        channel=channel,
        first_line=first_line.lstrip('# ').strip(),
    )


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

    conv_dir = _conv_dir(user)
    if not conv_dir.exists():
        return ConversationListResponse(conversations=[])

    # List .md files (excluding index.md), sort by name descending (newest first)
    files = sorted(
        (f for f in conv_dir.glob('*.md') if f.name != 'index.md'),
        key=lambda p: p.name,
        reverse=True,
    )

    entries = []
    for f in files[:limit]:
        if entry := _parse_conv_file(f):
            entries.append(entry)

    return ConversationListResponse(conversations=entries)


_TURN_MARKER_RE = re.compile(r'\n\n\*\*(?:Marcel|User):\*\* ')


def _extract_assistant_message(raw: str, turn: int | None = None) -> str | None:
    """Extract an assistant message from conversation markdown.

    Args:
        raw: Full conversation markdown text.
        turn: 0-based index of the assistant turn to extract. When ``None``,
            returns the last assistant message (backwards-compatible default).
    """
    marker = '**Marcel:** '
    if turn is None:
        # Last assistant message
        idx = raw.rfind(marker)
        if idx < 0:
            return None
        start = idx + len(marker)
    else:
        # Find the nth assistant message (0-based)
        offset = 0
        for _ in range(turn + 1):
            idx = raw.find(marker, offset)
            if idx < 0:
                return None
            offset = idx + len(marker)
        start = offset

    # The block ends at the next turn marker (**Marcel:** or **User:**) or EOF
    m = _TURN_MARKER_RE.search(raw, start)
    return raw[start : m.start()].strip() if m else raw[start:].strip()


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

    When *turn* is provided (0-based), returns that specific assistant
    message.  Otherwise returns the last one (backwards compatible).

    Authenticates via Telegram ``initData`` (Mini App) or Bearer token.
    """
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

    raw = load_conversation(user_slug, conversation_id)
    if not raw:
        raise HTTPException(status_code=404, detail='Conversation not found')

    content = _extract_assistant_message(raw, turn=turn)
    if content is None:
        raise HTTPException(status_code=404, detail='No assistant message found')

    return MessageResponse(content=content)
