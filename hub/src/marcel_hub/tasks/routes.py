"""The `/api/tasks` routes from `contracts/app-api.yaml`.

Errors, including a bad request body or query, answer with the contract's `Error` shape and the
contract's status codes (400, 404, 409), not FastAPI's default 422.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from marcel_hub.models import Task, TaskState
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.tasks.registry import NotArchivable, TaskFinished, TaskNotFound, TaskRegistry

router = APIRouter(prefix='/api')


class NewMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)
    client_id: str | None = None


class HandBack(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str | None = None


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'code': code, 'message': message}, status_code=status)


def bad_request(message: str) -> JSONResponse:
    return error(400, 'bad_request', message)


def _registry(request: Request) -> TaskRegistry:
    return request.app.state.tasks


async def _answer(
    registry: TaskRegistry, action: Callable[[], Task | Awaitable[Task]], status: int = 200
) -> JSONResponse:
    """Run a registry action and answer with the task, or with the contract error it maps to."""
    try:
        result = action()
        task = await result if inspect.isawaitable(result) else result
    except TaskNotFound as exc:
        return error(404, 'task_not_found', str(exc))
    except TaskFinished as exc:
        return error(409, 'task_finished', str(exc))
    except NotArchivable as exc:
        return error(409, 'task_not_finished', str(exc))
    except RunnerUnavailable as exc:
        return error(503, 'runner_unreachable', f'The task was not stopped. {exc}')
    return JSONResponse(registry.to_api(task), status_code=status)


async def _body[M: BaseModel](request: Request, model: type[M], required: bool) -> M | JSONResponse:
    raw = await request.body()
    if not raw.strip():
        return bad_request('The request needs a JSON body.') if required else model()
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = '.'.join(str(p) for p in first['loc']) or 'body'
        return bad_request(f'{where}: {first["msg"]}.')


@router.get('/tasks')
def list_tasks(
    request: Request,
    agent_id: str | None = None,
    state: Annotated[list[str] | None, Query()] = None,
    archived: str = 'false',
    cursor: str | None = None,
    limit: str = '50',
) -> Any:
    if not agent_id:
        return bad_request('agent_id is required.')
    try:
        states = [TaskState(s) for s in state or []]
    except ValueError:
        return bad_request(f'state must be one of {", ".join(s.value for s in TaskState)}.')
    if archived not in ('true', 'false'):
        return bad_request('archived must be true or false.')
    if not limit.isdigit() or not 1 <= int(limit) <= 200:
        return bad_request('limit must be a number from 1 to 200.')
    if cursor is not None and not cursor.isdigit():
        return bad_request('That page cursor is not valid. Start again without it.')
    registry = _registry(request)
    tasks, nxt = registry.list_tasks(
        agent_id, states, archived == 'true', int(cursor or 0), int(limit)
    )
    page: dict[str, Any] = {'items': [registry.to_api(t) for t in tasks]}
    if nxt is not None:
        page['next_cursor'] = str(nxt)
    return page


@router.get('/tasks/{task_id}')
async def get_task(request: Request, task_id: str) -> Any:
    registry = _registry(request)
    return await _answer(registry, lambda: registry.get(task_id))


@router.get('/tasks/{task_id}/events')
def list_task_events(request: Request, task_id: str, after: str = '0', limit: str = '50') -> Any:
    if not after.isdigit():
        return bad_request('after must be a whole number, 0 or more.')
    if not limit.isdigit() or not 1 <= int(limit) <= 200:
        return bad_request('limit must be a number from 1 to 200.')
    registry = _registry(request)
    try:
        events, more = registry.events(task_id, int(after), int(limit))
    except TaskNotFound as exc:
        return error(404, 'task_not_found', str(exc))
    return {'items': [registry.event_to_api(e) for e in events], 'has_more': more}


@router.post('/tasks/{task_id}/messages')
async def steer_task(request: Request, task_id: str) -> Any:
    body = await _body(request, NewMessage, required=True)
    if isinstance(body, JSONResponse):
        return body
    registry = _registry(request)
    return await _answer(registry, lambda: registry.steer(task_id, body.text), 202)


@router.post('/tasks/{task_id}/stop')
async def stop_task(request: Request, task_id: str) -> Any:
    registry = _registry(request)
    return await _answer(registry, lambda: registry.stop(task_id))


@router.post('/tasks/{task_id}/archive')
async def archive_task(request: Request, task_id: str) -> Any:
    registry = _registry(request)
    return await _answer(registry, lambda: registry.archive(task_id))


@router.post('/tasks/{task_id}/handback')
async def hand_back_task(request: Request, task_id: str) -> Any:
    body = await _body(request, HandBack, required=False)
    if isinstance(body, JSONResponse):
        return body
    registry = _registry(request)
    return await _answer(registry, lambda: registry.handback(task_id, body.note), 202)
