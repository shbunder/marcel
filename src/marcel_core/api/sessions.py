"""REST endpoints for session management (v2 history).

Provides listing, creation, and deletion of per-session conversation files.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from marcel_core.auth import valid_user_slug, verify_api_token
from marcel_core.memory.history import create_session, delete_session, list_sessions

log = logging.getLogger(__name__)

router = APIRouter(prefix='/v2', tags=['sessions'])


class SessionEntry(BaseModel):
    session_id: str
    channel: str
    created_at: str
    last_active: str
    message_count: int
    title: str | None


class SessionListResponse(BaseModel):
    sessions: list[SessionEntry]


class SessionCreateRequest(BaseModel):
    channel: str = 'default'
    session_id: str | None = None
    title: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    channel: str
    created_at: str


@router.get('/sessions', response_model=SessionListResponse)
async def list_user_sessions(
    user: str = Query(..., description='User slug'),
    channel: str | None = Query(None, description='Filter by channel'),
    limit: int = Query(50, ge=1, le=200),
    authorization: str = Header(''),
) -> SessionListResponse:
    """List sessions for a user, newest first."""
    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not valid_user_slug(user):
        raise HTTPException(status_code=400, detail='Invalid user slug')

    sessions = list_sessions(user, channel=channel, limit=limit)
    return SessionListResponse(
        sessions=[
            SessionEntry(
                session_id=s.session_id,
                channel=s.channel,
                created_at=s.created_at.isoformat(),
                last_active=s.last_active.isoformat(),
                message_count=s.message_count,
                title=s.title,
            )
            for s in sessions
        ]
    )


@router.post('/sessions', response_model=SessionCreateResponse)
async def create_user_session(
    user: str = Query(..., description='User slug'),
    body: SessionCreateRequest = SessionCreateRequest(),
    authorization: str = Header(''),
) -> SessionCreateResponse:
    """Create a new conversation session."""
    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not valid_user_slug(user):
        raise HTTPException(status_code=400, detail='Invalid user slug')

    meta = create_session(user, body.channel, session_id=body.session_id, title=body.title)
    return SessionCreateResponse(
        session_id=meta.session_id,
        channel=meta.channel,
        created_at=meta.created_at.isoformat(),
    )


@router.delete('/sessions/{session_id}')
async def delete_user_session(
    session_id: str,
    user: str = Query(..., description='User slug'),
    channel: str = Query(..., description='Channel'),
    authorization: str = Header(''),
) -> dict:
    """Delete a conversation session."""
    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    if not valid_user_slug(user):
        raise HTTPException(status_code=400, detail='Invalid user slug')

    deleted = delete_session(user, channel, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail='Session not found')
    return {'deleted': True}
