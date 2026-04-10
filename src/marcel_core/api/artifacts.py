"""REST endpoints for rich-content artifacts.

Artifacts are created by the Telegram webhook when a response contains
rich content (calendars, checklists, tables, images). The Mini App
fetches them for the viewer and gallery views.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from marcel_core.auth import verify_api_token, verify_telegram_init_data
from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug
from marcel_core.storage.artifacts import (
    ArtifactSummary,
    files_dir,
    list_artifacts,
    load_artifact,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helper (same pattern as conversations.py)
# ---------------------------------------------------------------------------


def _authenticate(init_data: str, authorization: str) -> str:
    """Return the authenticated user_slug, or raise 401."""
    if init_data:
        tg_user = verify_telegram_init_data(init_data)
        if tg_user is None:
            raise HTTPException(status_code=401, detail='Invalid Telegram credentials')
        user_slug = get_telegram_user_slug(tg_user['id'])
        if user_slug is None:
            raise HTTPException(status_code=401, detail='Telegram user not linked')
        return user_slug

    token = authorization.removeprefix('Bearer ').strip()
    if not verify_api_token(token):
        raise HTTPException(status_code=401, detail='Unauthorized')
    raise HTTPException(status_code=400, detail='initData required for this endpoint')


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class ArtifactResponse(BaseModel):
    id: str
    content_type: str
    content: str
    title: str
    created_at: str


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummary]


@router.get('/api/artifact/{artifact_id}', response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: str,
    initData: str = Query(''),
    authorization: str = Header(''),
) -> ArtifactResponse:
    """Fetch a single artifact by ID."""
    user_slug = _authenticate(initData, authorization)

    artifact = load_artifact(artifact_id)
    if artifact is None or artifact.user_slug != user_slug:
        raise HTTPException(status_code=404, detail='Artifact not found')

    return ArtifactResponse(
        id=artifact.id,
        content_type=artifact.content_type,
        content=artifact.content,
        title=artifact.title,
        created_at=artifact.created_at.isoformat(),
    )


@router.get('/api/artifacts', response_model=ArtifactListResponse)
async def list_artifacts_endpoint(
    initData: str = Query(''),
    authorization: str = Header(''),
    conversation: str = Query('', description='Filter by conversation ID'),
    limit: int = Query(20, ge=1, le=100),
) -> ArtifactListResponse:
    """List artifact summaries for the authenticated user."""
    user_slug = _authenticate(initData, authorization)

    items = list_artifacts(
        user_slug,
        conversation_id=conversation or None,
        limit=limit,
    )
    return ArtifactListResponse(artifacts=items)


@router.get('/api/artifact/{artifact_id}/file')
async def get_artifact_file(
    artifact_id: str,
    initData: str = Query(''),
    authorization: str = Header(''),
) -> FileResponse:
    """Serve the binary file for an image artifact."""
    user_slug = _authenticate(initData, authorization)

    artifact = load_artifact(artifact_id)
    if artifact is None or artifact.user_slug != user_slug:
        raise HTTPException(status_code=404, detail='Artifact not found')
    if artifact.content_type != 'image':
        raise HTTPException(status_code=400, detail='Artifact is not an image')

    # content stores the filename relative to the files directory
    file_path = files_dir() / artifact.content
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found')

    return FileResponse(str(file_path))
