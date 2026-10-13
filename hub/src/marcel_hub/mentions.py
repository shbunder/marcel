"""Mentions (B-42, S-18.2): `@` a quest or an item, `#` a project, in the composer.

- `GET /api/mentions?q=&kind=` lists the caller's own quests, projects and items whose title holds
  `q` (any case), newest change first, at most 20. Archived quests and projects, and boards (a
  project's, not an item to mention), are left out.
- `resolve(...)` checks a message's `mentions` before it is stored: each must be the agent's own
  and not archived, or the message is refused with 404 `mention_not_found` and nothing is
  stored. The stored copy carries the hub's current titles.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlmodel import Session, col, select

from marcel_hub.auth import ApiError, current_device, require_device
from marcel_hub.auth.deps import DbSession
from marcel_hub.models import Agent, Artifact, ArtifactKind, Project, Task

router = APIRouter(prefix='/api', tags=['mentions'], dependencies=[Depends(require_device)])

LIMIT = 20
Kind = Literal['task', 'project', 'artifact']


class Mention(BaseModel):
    """The contract's `Mention`, as a message carries it."""

    model_config = ConfigDict(extra='forbid')
    kind: Kind
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)


def _like(text: str) -> str:
    """`text` as a LIKE pattern that matches it literally, anywhere, in any case."""
    escaped = text.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def _agent_ids(session: Session, user_id: str) -> list[str]:
    return list(session.exec(select(Agent.id).where(Agent.owner_user_id == user_id)))


def _projects(session: Session, agent_ids: list[str]) -> dict[str, Project]:
    """The agents' live projects, by id: an item belongs to its project's agent."""
    rows = session.exec(
        select(Project).where(col(Project.agent_id).in_(agent_ids), Project.archived == False)  # noqa: E712
    )
    return {p.id: p for p in rows}


def search(session: Session, user_id: str, q: str, kind: str | None) -> list[dict[str, Any]]:
    agent_ids = _agent_ids(session, user_id)
    pattern = _like(q)
    found: list[tuple[datetime, dict[str, Any]]] = []
    if kind in (None, 'task'):
        tasks = session.exec(
            select(Task).where(
                col(Task.agent_id).in_(agent_ids),
                col(Task.archived_at).is_(None),
                col(Task.title).ilike(pattern, escape='\\'),
            )
        )
        found += [(t.updated_at, {'kind': 'task', 'id': t.id, 'title': t.title}) for t in tasks]
    projects = _projects(session, agent_ids)
    if kind in (None, 'project'):
        found += [
            (p.updated_at, {'kind': 'project', 'id': p.id, 'title': p.name})
            for p in projects.values()
            if q.lower() in p.name.lower()
        ]
    if kind in (None, 'artifact'):
        # An item belongs to its project's agent, or to its task's when it has no project.
        own_tasks = select(Task.id).where(col(Task.agent_id).in_(agent_ids))
        items = session.exec(
            select(Artifact).where(
                or_(
                    col(Artifact.project_id).in_(list(projects)),
                    col(Artifact.project_id).is_(None) & col(Artifact.task_id).in_(own_tasks),
                ),
                Artifact.kind != ArtifactKind.board,
                col(Artifact.title).ilike(pattern, escape='\\'),
            )
        )
        found += [(a.updated_at, {'kind': 'artifact', 'id': a.id, 'title': a.title}) for a in items]
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [mention for _, mention in found[:LIMIT]]


def _title(session: Session, mention: Mention, agent_id: str) -> str | None:
    """The hub's title for a mention of this agent's, or None when it is not the agent's, or is
    archived (search no longer offers it)."""
    if mention.kind == 'task':
        task = session.get(Task, mention.id)
        ok = task is not None and task.agent_id == agent_id and task.archived_at is None
        return task.title if ok and task is not None else None
    if mention.kind == 'project':
        project = session.get(Project, mention.id)
        ok = project is not None and project.agent_id == agent_id and not project.archived
        return project.name if ok and project is not None else None
    from marcel_hub.conversations import agent_of_artifact

    art = session.get(Artifact, mention.id)
    return art.title if art is not None and agent_of_artifact(session, art) == agent_id else None


def resolve(session: Session, mentions: list[Mention], agent_id: str) -> list[dict[str, Any]]:
    """The mentions as stored: each the agent's own, with the hub's title. Another person's, or
    one that does not exist, refuses the whole message with 404 `mention_not_found`."""
    resolved = []
    for mention in mentions:
        title = _title(session, mention, agent_id)
        if title is None:
            raise ApiError(
                404,
                'mention_not_found',
                'Something you mentioned is not on this hub any more. Pick it again from the list.',
            )
        resolved.append({'kind': mention.kind, 'id': mention.id, 'title': title})
    return resolved


@router.get('/mentions')
def search_mentions(
    request: Request,
    session: DbSession,
    q: str = Query(default='', max_length=100),
    kind: Kind | None = None,
) -> list[dict[str, Any]]:
    """The caller's own quests, projects and items to mention, newest first, at most 20."""
    return search(session, current_device(request).user_id, q, kind)
