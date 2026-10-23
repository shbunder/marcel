"""`/api/artifacts`: the Items tab, one artifact, its content (by version), its versions, and
pinning it to another project (`POST /api/artifacts/{id}/move`)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from marcel_hub.artifacts.store import (
    ArtifactIsBoard,
    ArtifactNotFound,
    ArtifactStore,
    MoveTargetArchived,
    MoveTargetNotFound,
    NoContent,
    VersionNotFound,
)
from marcel_hub.auth import require_device
from marcel_hub.models import ArtifactKind
from marcel_hub.tasks.routes import _body  # pyright: ignore[reportPrivateUsage]

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def _not_found(artifact_id: str) -> JSONResponse:
    return _error(404, 'artifact_not_found', f'There is no item {artifact_id}. It may be gone.')


def _store(request: Request) -> ArtifactStore:
    return request.app.state.artifacts


def _missing(request: Request, artifact_id: str) -> JSONResponse | None:
    """404 `artifact_not_found` when there is no such item. Any paired phone reaches every
    buddy's items (ADR-013)."""
    try:
        _store(request).get(artifact_id)
    except ArtifactNotFound:
        return _not_found(artifact_id)
    return None


@router.get('/artifacts')
def list_artifacts(
    request: Request,
    task_id: str | None = None,
    project_id: str | None = None,
    kind: str | None = None,
    cursor: str | None = None,
    limit: str = '50',
) -> Any:
    kinds = [k.value for k in ArtifactKind]
    if kind is not None and kind not in kinds:
        return _error(400, 'bad_request', f'kind must be one of {", ".join(kinds)}.')
    if not limit.isdigit() or not 1 <= int(limit) <= 200:
        return _error(400, 'bad_request', 'limit must be a number from 1 to 200.')
    if cursor is not None and not cursor.isdigit():
        return _error(400, 'bad_request', 'That page cursor is not valid. Start again without it.')
    store = _store(request)
    rows, nxt = store.page(
        task_id,
        ArtifactKind(kind) if kind else None,
        int(cursor or 0),
        int(limit),
        project_id,
    )
    page: dict[str, Any] = {'items': [store.to_api(a) for a in rows]}
    if nxt is not None:
        page['next_cursor'] = str(nxt)
    return page


@router.get('/artifacts/{artifact_id}')
def get_artifact(request: Request, artifact_id: str) -> Any:
    if (refused := _missing(request, artifact_id)) is not None:
        return refused
    try:
        return _store(request).to_api(_store(request).get(artifact_id))
    except ArtifactNotFound:
        return _not_found(artifact_id)


@router.get('/artifacts/{artifact_id}/versions')
def list_versions(request: Request, artifact_id: str) -> Any:
    if (refused := _missing(request, artifact_id)) is not None:
        return refused
    store = _store(request)
    try:
        return [store.version_to_api(v) for v in store.versions(artifact_id)]
    except ArtifactNotFound:
        return _not_found(artifact_id)


@router.get('/artifacts/{artifact_id}/content')
def get_artifact_content(
    request: Request, artifact_id: str, version: Annotated[int | None, Query(ge=1)] = None
) -> Any:
    if (refused := _missing(request, artifact_id)) is not None:
        return refused
    try:
        path, art, content_type = _store(request).content_path(artifact_id, version)
    except ArtifactNotFound:
        return _not_found(artifact_id)
    except VersionNotFound:
        return _error(404, 'not_found', f'That item has no version {version}.')
    except NoContent as exc:
        return _error(404, 'not_found', str(exc))
    # The hub never renders content. Untrusted HTML opened straight from this URL runs in a
    # sandbox with no scripts; even trusted HTML gets an origin of its own.
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': 'sandbox allow-scripts' if art.trusted else 'sandbox',
    }
    return FileResponse(path, media_type=content_type, headers=headers)


class ArtifactMove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str


@router.post('/artifacts/{artifact_id}/move')
async def move_artifact(request: Request, artifact_id: str) -> Any:
    """Pin an item to another project of its agent (B-21, B-31). Marcel is not woken. Both
    projects are announced (`project.updated`), which also rebuilds both boards."""
    body = await _body(request, ArtifactMove, required=True)
    if isinstance(body, JSONResponse):
        return body
    store = _store(request)
    try:
        art, source = store.move(artifact_id, body.project_id)
    except ArtifactNotFound:
        return _not_found(artifact_id)
    except MoveTargetNotFound:
        return _error(404, 'project_not_found', 'That project is not on this hub.')
    except ArtifactIsBoard:
        return _error(409, 'artifact_is_board', 'A project’s board stays with its project.')
    except MoveTargetArchived as exc:
        return _error(
            409,
            'project_archived',
            f'“{exc}” is archived, so nothing new can be filed there. Pick a live project.',
        )
    if source is not None:
        projects = getattr(request.app.state, 'projects', None)
        if projects is not None:
            projects.announce(art.project_id)
            projects.announce(source)
    return store.to_api(art)
