"""`/api/projects` and `POST /api/tasks/{id}/move` from `contracts/app-api.yaml`.

Every phone sees every buddy's projects (ADR-013); an unknown id gets 404 `project_not_found`.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import AnyUrl, BaseModel, ConfigDict, Field

from marcel_hub.auth import require_device
from marcel_hub.models import Project, Task
from marcel_hub.projects.store import (
    AgentNotFound,
    NotArchivable,
    ProjectArchived,
    ProjectNotFound,
    ProjectStore,
    SlugTaken,
)
from marcel_hub.tasks.registry import TaskNotFound, TaskRegistry
from marcel_hub.tasks.routes import _body, bad_request, error  # pyright: ignore[reportPrivateUsage]

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])

SLUG = r'^[a-z0-9][a-z0-9-]{0,39}$'  # the contract's


class NewProject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str
    name: str = Field(min_length=1, max_length=60)
    slug: str | None = Field(default=None, pattern=SLUG)
    goal: str | None = None
    repo: str | None = None
    folder: str | None = None
    instructions: str | None = None
    claude_project_url: AnyUrl | None = None


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=60)
    goal: str | None = None
    repo: str | None = None
    folder: str | None = None
    instructions: str | None = None
    claude_project_url: AnyUrl | None = None


class TaskMove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str


def _store(request: Request) -> ProjectStore:
    return request.app.state.projects


def _fields(model: BaseModel) -> dict[str, Any]:
    """The fields that were sent, URLs as text."""
    return {k: str(v) if isinstance(v, AnyUrl) else v
            for k, v in model.model_dump(exclude_unset=True).items()}  # fmt: skip


def _answer(request: Request, action: Callable[[], Project], status: int = 200) -> JSONResponse:
    store = _store(request)
    try:
        project = action()
    except ProjectNotFound:
        return error(404, 'project_not_found', 'That project is not on this hub.')
    except AgentNotFound:
        return error(404, 'agent_not_found', 'That agent is not on this hub.')
    except SlugTaken as exc:
        return error(409, 'slug_taken', f'A live project is already called “{exc}”. Pick another '
                                        'slug, or archive that project first.')  # fmt: skip
    except NotArchivable as exc:
        return error(409, exc.code, str(exc))
    return JSONResponse(store.to_api(project), status_code=status)


@router.get('/projects')
def list_projects(request: Request, agent_id: str | None = None, archived: str = 'false') -> Any:
    if not agent_id:
        return bad_request('agent_id is required.')
    if archived not in ('true', 'false'):
        return bad_request('archived must be true or false.')
    return _store(request).list_projects(agent_id, archived == 'true')


@router.post('/projects')
async def create_project(request: Request) -> Any:
    body = await _body(request, NewProject, required=True)
    if isinstance(body, JSONResponse):
        return body
    fields = _fields(body)
    agent_id, name, slug = fields.pop('agent_id'), fields.pop('name'), fields.pop('slug', None)
    return _answer(request, lambda: _store(request).create(agent_id, name, slug, **fields), 201)


@router.get('/projects/{project_id}')
def get_project(request: Request, project_id: str) -> Any:
    return _answer(request, lambda: _store(request).get(project_id))


@router.patch('/projects/{project_id}')
async def update_project(request: Request, project_id: str) -> Any:
    body = await _body(request, ProjectUpdate, required=True)
    if isinstance(body, JSONResponse):
        return body
    changes = _fields(body)
    if not changes:
        return bad_request('Send at least one field to change.')
    if 'name' in changes and changes['name'] is None:
        return bad_request('name cannot be empty.')
    return _answer(request, lambda: _store(request).update(project_id, changes))


@router.post('/projects/{project_id}/archive')
def archive_project(request: Request, project_id: str) -> Any:
    return _answer(request, lambda: _store(request).archive(project_id))


@router.post('/tasks/{task_id}/move')
async def move_task(request: Request, task_id: str) -> Any:
    body = await _body(request, TaskMove, required=True)
    if isinstance(body, JSONResponse):
        return body
    registry: TaskRegistry = request.app.state.tasks
    try:
        task: Task = _store(request).move(task_id, body.project_id)
    except TaskNotFound as exc:
        return error(404, 'task_not_found', str(exc))
    except ProjectNotFound:
        return error(404, 'project_not_found', 'That project is not on this hub.')
    except ProjectArchived as exc:
        return error(409, 'project_archived', str(exc))
    return JSONResponse(registry.to_api(registry.get(task.id)))
