"""Artifacts (B-21, B-34, B-36): the store (`store.py`) and `/api/artifacts` (`routes.py`)."""

from pathlib import Path

from fastapi import FastAPI

from marcel_hub.artifacts.routes import router
from marcel_hub.artifacts.store import (
    FILE_KINDS,
    HUB_AUTHOR,
    MAX_BLOB_BYTES,
    SELF_REPO,
    ArtifactIsBoard,
    ArtifactNotFound,
    ArtifactRefused,
    ArtifactStore,
    Content,
    MoveTargetArchived,
    MoveTargetNotFound,
    NoContent,
    NoPrStatus,
    PrStatusSource,
    PrStatusUnavailable,
    UnknownArtifact,
    VersionNotFound,
    blob_name,
    check_dashboard,
    is_self_change_url,
    pr_status_from_gh,
)

__all__ = [
    'FILE_KINDS',
    'HUB_AUTHOR',
    'MAX_BLOB_BYTES',
    'SELF_REPO',
    'ArtifactIsBoard',
    'ArtifactNotFound',
    'ArtifactRefused',
    'ArtifactStore',
    'Content',
    'MoveTargetArchived',
    'MoveTargetNotFound',
    'NoContent',
    'NoPrStatus',
    'PrStatusSource',
    'PrStatusUnavailable',
    'UnknownArtifact',
    'VersionNotFound',
    'blob_name',
    'check_dashboard',
    'install',
    'is_self_change_url',
    'pr_status_from_gh',
]


def install(app: FastAPI, files_root: Path | None = None) -> ArtifactStore:
    """Put the store on `app.state.artifacts` and mount `/api/artifacts`. Call after
    `tasks.install`: an artifact handed in by a task is recorded on that task."""
    store = ArtifactStore(
        app.state.engine,
        app.state.settings.data_dir / 'artifacts',
        tasks=getattr(app.state, 'tasks', None),
        files_root=files_root,
        events=getattr(app.state, 'events', None),
    )
    app.state.artifacts = store
    app.include_router(router)
    return store
