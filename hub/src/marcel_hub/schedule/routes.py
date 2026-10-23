"""`/api/schedules` from `contracts/app-api.yaml`: list, create, pause / resume / change, delete.

Only the owner of a schedule's agent sees it; anyone else gets 404 `schedule_not_found`.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from marcel_hub.auth import require_device
from marcel_hub.models import Agent
from marcel_hub.schedule.cron import CronError
from marcel_hub.schedule.scheduler import ScheduleBuiltin, ScheduleNotFound, Scheduler
from marcel_hub.tasks.routes import _body, bad_request, error  # pyright: ignore[reportPrivateUsage]

router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])

MAX_PROMPT = 20000
MAX_DEADLINE = 7 * 24 * 60  # a week, as for the brain's schedule_create


class NewSchedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str
    name: str = Field(min_length=1, max_length=120)
    cron: str = Field(min_length=1, max_length=120)
    tz: str = Field(min_length=1, max_length=64)
    location: Literal['brain', 'nuc', 'cloud']
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    deadline_minutes: int | None = Field(default=None, ge=1, le=MAX_DEADLINE)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=120)
    cron: str | None = Field(default=None, min_length=1, max_length=120)
    tz: str | None = Field(default=None, min_length=1, max_length=64)
    prompt: str | None = Field(default=None, min_length=1, max_length=MAX_PROMPT)
    paused: bool | None = None
    deadline_minutes: int | None = Field(default=None, ge=1, le=MAX_DEADLINE)


def _scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def _not_found() -> JSONResponse:
    return error(404, 'schedule_not_found', 'That schedule is not on this hub.')


def _is_buddy(request: Request, agent_id: str) -> bool:
    """`agent_id` is a buddy on this hub (every buddy is the one person's)."""
    with Session(request.app.state.engine) as s:
        agent = s.get(Agent, agent_id)
    return agent is not None


@router.get('/schedules')
def list_schedules(request: Request, agent_id: str | None = None) -> Any:
    if not agent_id:
        return bad_request('agent_id is required.')
    if not _is_buddy(request, agent_id):
        return []
    return _scheduler(request).list(agent_id)


@router.post('/schedules')
async def create_schedule(request: Request) -> Any:
    body = await _body(request, NewSchedule, required=True)
    if isinstance(body, JSONResponse):
        return body
    if not _is_buddy(request, body.agent_id):
        return bad_request('agent_id is not a buddy on this hub.')
    spec = body.model_dump(exclude_none=True, exclude={'agent_id'})
    try:
        created = _scheduler(request).create(body.agent_id, spec)
    except CronError as exc:
        return bad_request(str(exc))
    return JSONResponse(created, status_code=201)


@router.patch('/schedules/{schedule_id}')
async def update_schedule(request: Request, schedule_id: str) -> Any:
    body = await _body(request, ScheduleUpdate, required=True)
    if isinstance(body, JSONResponse):
        return body
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return bad_request('Send at least one field to change.')
    nulls = sorted(k for k, v in changes.items() if v is None)
    if nulls:
        return bad_request(f'{", ".join(nulls)} cannot be empty.')
    scheduler = _scheduler(request)
    try:
        scheduler.existing(schedule_id)
        return scheduler.update(schedule_id, changes)
    except ScheduleNotFound:
        return _not_found()
    except CronError as exc:
        return bad_request(str(exc))


@router.delete('/schedules/{schedule_id}')
def delete_schedule(request: Request, schedule_id: str) -> Any:
    scheduler = _scheduler(request)
    try:
        row = scheduler.existing(schedule_id)
        scheduler.delete(row.agent_id, schedule_id)
    except ScheduleNotFound:
        return _not_found()
    except ScheduleBuiltin as exc:
        return error(409, 'schedule_builtin', str(exc))
    return Response(status_code=204)
