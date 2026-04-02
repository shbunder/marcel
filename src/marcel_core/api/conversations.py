"""REST endpoint for listing conversations."""

import pathlib

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from marcel_core.auth import valid_user_slug, verify_api_token
from marcel_core.storage.conversations import _conv_dir

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
