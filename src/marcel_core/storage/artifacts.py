"""Artifact storage for rich content served by the Telegram Mini App.

Each artifact is a JSON file at ``data_root() / "artifacts" / "{id}.json"``.
Image binaries go to ``data_root() / "artifacts" / "files" / "{id}.ext"``.
"""

import pathlib
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from marcel_core.storage._atomic import atomic_write
from marcel_core.storage._root import data_root

ContentType = Literal['markdown', 'image', 'chart_data', 'html', 'checklist', 'calendar']


class Artifact(BaseModel):
    """A stored rich-content artifact."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_slug: str
    conversation_id: str
    content_type: ContentType
    content: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArtifactSummary(BaseModel):
    """Lightweight projection for gallery listings."""

    id: str
    title: str
    content_type: ContentType
    created_at: datetime


def _artifacts_dir() -> pathlib.Path:
    return data_root() / 'artifacts'


def _artifact_path(artifact_id: str) -> pathlib.Path:
    return _artifacts_dir() / f'{artifact_id}.json'


def files_dir() -> pathlib.Path:
    """Return the directory for binary artifact files (images, etc.)."""
    d = _artifacts_dir() / 'files'
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_artifact(artifact: Artifact) -> str:
    """Persist an artifact to disk. Returns the artifact ID."""
    atomic_write(_artifact_path(artifact.id), artifact.model_dump_json(indent=2))
    return artifact.id


def load_artifact(artifact_id: str) -> Artifact | None:
    """Load an artifact by ID, or ``None`` if not found."""
    path = _artifact_path(artifact_id)
    if not path.exists():
        return None
    try:
        return Artifact.model_validate_json(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def list_artifacts(
    user_slug: str,
    *,
    conversation_id: str | None = None,
    limit: int = 50,
) -> list[ArtifactSummary]:
    """List artifacts for a user, newest first.

    Optionally filter by ``conversation_id``.
    """
    artifacts_dir = _artifacts_dir()
    if not artifacts_dir.exists():
        return []

    results: list[Artifact] = []
    for path in artifacts_dir.glob('*.json'):
        try:
            a = Artifact.model_validate_json(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if a.user_slug != user_slug:
            continue
        if conversation_id and a.conversation_id != conversation_id:
            continue
        results.append(a)

    results.sort(key=lambda a: a.created_at, reverse=True)
    return [
        ArtifactSummary(
            id=a.id,
            title=a.title,
            content_type=a.content_type,
            created_at=a.created_at,
        )
        for a in results[:limit]
    ]


def create_artifact(
    user_slug: str,
    conversation_id: str,
    content_type: ContentType,
    content: str,
    title: str,
) -> str:
    """Create and persist a new artifact. Returns the artifact ID."""
    artifact = Artifact(
        user_slug=user_slug,
        conversation_id=conversation_id,
        content_type=content_type,
        content=content,
        title=title,
    )
    return save_artifact(artifact)
