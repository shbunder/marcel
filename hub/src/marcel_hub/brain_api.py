"""`/brain/…`: the hub side of Marcel's brain tools (S-05.3, `plugins/marcel/tools`).

The brain session's MCP tools server (`marcel-tools`) calls these routes over plain HTTP on the
NUC. They are the brain's hands: start, steer, stop and file tasks; keep projects; publish
artifacts; read and write memory; schedules; a push to the owner; plan usage.

Who may call
------------
- **Not through the tunnel.** The routes sit outside `/api`, and the Cloudflare tunnel forwards only
  `/api/*`. As a backstop (as on `/channel`), a request carrying Cloudflare's `cf-connecting-ip`
  header is answered 404, like a route that does not exist.
- **The hub secret.** `X-Marcel-Secret` must match the hub secret (`<data_dir>/hub-secret`, the one
  the channel uses; the plugin reads it from `$CLAUDE_PLUGIN_DATA/hub-secret`). No device token is
  accepted here and the secret is accepted nowhere under `/api`.
- **A brain session.** `X-Marcel-Session` is the caller's `CLAUDE_CODE_SESSION_ID`. It must be the
  session the runner reported for a conversation (`Conversation.brain_session_id`): the brain of
  the main conversation, or a side thread forked from it. A worker's session is refused (403): a
  worker reports through the channel and never starts or stops other work.
- **Its own agent only.** Every task, project and schedule is looked up within the caller's agent.
  Anything else answers 404, exactly like something that does not exist.

Errors answer `{code, message}`; the message is written for the brain to act on.

Seams for later stories
-----------------------
Some tools reach parts of the hub that are built elsewhere. Each sits behind a small interface on
`app.state.brain_ports`; until its story plugs in the real one, the default answers 503
`not_set_up` with a plain sentence, and nothing is stored:

- `boards` (`board_note`): project boards, S-04.11.
- `artifacts` (`artifacts_publish`): the default stores through the artifact store's
  `publish_reported` (the same path as a worker's `report`), so a new version (`artifact_id`) is
  refused by the store until S-04.11 adds versions, and an artifact can be filed only in the
  inbox until the store takes a project.
- `schedules` (`schedule_*`): the scheduler, S-09.1.
- `usage` (`usage_get`): the usage guard, S-10.1.
"""

import hmac
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated, Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AnyUrl, BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from marcel_hub.artifacts import ArtifactRefused, ArtifactStore
from marcel_hub.auth import ApiError
from marcel_hub.channel.gateway import CHAT_ROLES, MIN_SECRET, Gateway
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.memory import (
    MAX_FILE_BYTES,
    InvalidMemoryPath,
    MemoryConflict,
    MemoryFileNotFound,
    MemoryFileTooBig,
    MemoryRepo,
    MemoryUnavailable,
)
from marcel_hub.models import (
    INBOX_SLUG,
    Agent,
    MessageKind,
    MessageRole,
    Project,
    Task,
    TaskEvent,
    TaskLocation,
    TaskOrigin,
    TaskState,
    utcnow,
)
from marcel_hub.notify.message import BODY_MAX, TITLE_MAX, PushNotification, clip, message_link
from marcel_hub.projects.store import ProjectArchived, ProjectStore, SlugTaken
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.tasks.registry import TaskFinished, TaskNotFound, TaskRegistry
from marcel_hub.tasks.routes import _body, bad_request, error  # pyright: ignore[reportPrivateUsage]

log = logging.getLogger(__name__)

TUNNEL_HEADER = 'cf-connecting-ip'
SECRET_HEADER = 'x-marcel-secret'
SESSION_HEADER = 'x-marcel-session'
SLUG = r'^[a-z0-9][a-z0-9-]{0,39}$'
MODEL = r'^[A-Za-z0-9._\[\]][A-Za-z0-9._\[\]-]{0,79}$'  # runner-api.yaml's
REPO = r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
MAX_TEXT = 20000
LIST_LIMIT = 50


# Who is calling -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Brain:
    """The brain (or side-thread) session making the request, and the agent it acts for."""

    session_id: str
    role: str
    agent_id: str
    owner_user_id: str
    conversation_id: str


def _hidden() -> ApiError:
    return ApiError(404, 'not_found', 'Not found.')


def _hub_secret(gateway: Gateway) -> str | None:
    try:
        secret = gateway.secret_path.read_text().strip()
    except OSError:
        return None
    return secret if len(secret) >= MIN_SECRET else None


def require_brain(request: Request) -> Brain:
    """The brain session behind this request, or an error. Put it on every `/brain` route."""
    if TUNNEL_HEADER in request.headers:
        log.warning('Refused a /brain request that came through the tunnel.')
        raise _hidden()
    gateway: Gateway = request.app.state.channel
    secret = _hub_secret(gateway)
    given = request.headers.get(SECRET_HEADER, '')
    if secret is None:
        raise ApiError(
            503,
            'no_secret',
            f'The hub has no secret in {gateway.secret_path}, so it refuses the brain tools. '
            'Redeploy Marcel to write it.',
        )
    if not hmac.compare_digest(secret.encode(), given.encode()):
        raise ApiError(401, 'bad_secret', 'The hub secret does not match this hub. Redeploy.')
    session_id = request.headers.get(SESSION_HEADER, '')
    peer = gateway.resolve(session_id) if session_id else None
    if peer is None:
        raise ApiError(
            401,
            'unknown_session',
            'This session is not Marcel’s brain: the hub has no conversation for it.',
        )
    if peer.role not in CHAT_ROLES:  # a worker or an adopted session
        raise ApiError(
            403,
            'not_brain',
            'Only Marcel’s brain can use these tools. A worker reports with the report tool.',
        )
    assert peer.conversation_id is not None  # brain and side sessions act for a conversation
    with Session(request.app.state.engine) as s:
        agent = s.get(Agent, peer.agent_id)
    if agent is None:  # pragma: no cover - a foreign key
        raise _hidden()
    return Brain(session_id, peer.role, agent.id, agent.owner_user_id, peer.conversation_id)


BrainDep = Annotated[Brain, Depends(require_brain)]

router = APIRouter(prefix='/brain', tags=['brain'])


# Seams --------------------------------------------------------------------------------------


class NotSetUp(Exception):
    """This part of the hub is not built yet. The message says what is missing (503)."""


class Boards(Protocol):
    def add_note(self, project_id: str, text: str) -> dict[str, Any]:
        """Add Marcel's line to the project's board. Returns `{project_id, …}`."""
        ...


class Schedules(Protocol):
    def create(self, agent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Create a schedule (app-api `NewSchedule` without `agent_id`); returns `Schedule`."""
        ...

    def list(self, agent_id: str) -> list[dict[str, Any]]: ...

    def delete(self, agent_id: str, schedule_id: str) -> None:
        """Raises `LookupError` for a schedule not on this agent, `PermissionError` for a
        built-in one (with a plain message)."""
        ...


class Usage(Protocol):
    def current(self) -> dict[str, Any]:
        """The app-api `Usage` reading."""
        ...


class BrainArtifacts(Protocol):
    def publish(self, spec: dict[str, Any], project_id: str, inbox: bool) -> dict[str, Any]:
        """Store an artifact Marcel hands in (channel.schema.json `Artifact`), filed under
        `project_id`. Returns the app-api `Artifact`. Raises `ArtifactRefused`."""
        ...


class NoBoards:
    def add_note(self, project_id: str, text: str) -> dict[str, Any]:
        raise NotSetUp('Project boards are not built yet, so the note was not kept.')


class NoSchedules:
    MESSAGE = 'The scheduler is not built yet, so schedules cannot be made, listed or deleted.'

    def create(self, agent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        raise NotSetUp(self.MESSAGE)

    def list(self, agent_id: str) -> list[dict[str, Any]]:
        raise NotSetUp(self.MESSAGE)

    def delete(self, agent_id: str, schedule_id: str) -> None:
        raise NotSetUp(self.MESSAGE)


class NoUsage:
    def current(self) -> dict[str, Any]:
        raise NotSetUp('The hub does not read plan usage yet, so there is no number to give.')


class ReportedArtifacts:
    """Marcel's artifacts through the artifact store, the same way a worker's are stored."""

    def __init__(self, store: ArtifactStore | None) -> None:
        self.store = store

    def publish(self, spec: dict[str, Any], project_id: str, inbox: bool) -> dict[str, Any]:
        if self.store is None:
            raise NotSetUp('This hub has no artifact store, so nothing was saved.')
        if not inbox:
            raise NotSetUp(
                'This hub cannot file Marcel’s own artifacts under a project yet. Leave out '
                '"project" to save it in the Inbox, then move it from the app.'
            )
        return self.store.to_api(self.store.publish_reported(spec))


@dataclass
class BrainPorts:
    artifacts: BrainArtifacts
    boards: Boards = field(default_factory=NoBoards)
    schedules: Schedules = field(default_factory=NoSchedules)
    usage: Usage = field(default_factory=NoUsage)


# Helpers ------------------------------------------------------------------------------------


def _ports(request: Request) -> BrainPorts:
    return request.app.state.brain_ports


def _tasks(request: Request) -> TaskRegistry:
    return request.app.state.tasks


def _projects(request: Request) -> ProjectStore:
    return request.app.state.projects


def not_set_up(exc: NotSetUp) -> JSONResponse:
    return error(503, 'not_set_up', str(exc))


def _task_not_found(task_id: str) -> JSONResponse:
    return error(404, 'task_not_found', f'There is no task {task_id}. tasks_list shows them.')


def _own_task(request: Request, brain: Brain, task_id: str) -> Task | None:
    """The task if it belongs to the brain's agent; None for anything else."""
    try:
        task = _tasks(request).get(task_id)
    except TaskNotFound:
        return None
    return task if task.agent_id == brain.agent_id else None


def _project_ref(request: Request, brain: Brain, ref: str) -> Project | JSONResponse:
    """A project of the brain's agent by id or slug; live ones first for a slug."""
    with Session(request.app.state.engine) as s:
        project = s.get(Project, ref)
        if project is None or project.agent_id != brain.agent_id:
            project = s.exec(
                select(Project)
                .where(Project.agent_id == brain.agent_id)
                .where(Project.slug == ref)
                .order_by(Project.archived)  # pyright: ignore[reportArgumentType]
            ).first()
    if project is None:
        return error(
            404, 'project_not_found', f'There is no project “{ref}”. projects_list shows them.'
        )
    return project


def _live_project(request: Request, brain: Brain, ref: str | None) -> Project | JSONResponse:
    if ref is None:
        return _projects(request).inbox(brain.agent_id)
    project = _project_ref(request, brain, ref)
    if isinstance(project, Project) and project.archived:
        return error(
            409,
            'project_archived',
            f'“{project.name}” is archived, so nothing new can be filed there. Pick a live one.',
        )
    return project


# Tasks --------------------------------------------------------------------------------------


class NewTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=MAX_TEXT)
    location: str
    project: str | None = Field(default=None, min_length=1)
    repo: str | None = Field(default=None, pattern=REPO)
    model: str | None = Field(default=None, pattern=MODEL)
    expected_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    message_id: str | None = Field(default=None, min_length=1)


class Steer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class Move(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project: str = Field(min_length=1)


@router.post('/tasks')
async def spawn_task(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewTask, required=True)
    if isinstance(body, JSONResponse):
        return body
    if body.location == 'auto':
        # The brain decides where work runs (B-04), so the decision shows in its own turn.
        return bad_request(
            'location "auto" is not accepted: decide yourself. Use "nuc" for anything that needs '
            'local files, Docker, the home network or the NUC’s tools, and "cloud" for work on a '
            'GitHub repository that needs nothing local.'
        )
    if body.location not in (TaskLocation.nuc.value, TaskLocation.cloud.value):
        return bad_request('location must be "nuc" or "cloud".')
    if body.location == TaskLocation.cloud.value and body.repo is None:
        return bad_request('A cloud task needs "repo" (owner/name): it works on GitHub only.')
    project = _live_project(request, brain, body.project)
    if isinstance(project, JSONResponse):
        return project
    with Session(request.app.state.engine) as s:
        agent = s.get(Agent, brain.agent_id)
    assert agent is not None
    fields: dict[str, Any] = {'project_id': project.id}
    if body.repo is not None:
        fields['repo'] = body.repo
    if body.message_id is not None:
        fields['parent_message_id'] = body.message_id
    if body.expected_minutes is not None:
        fields['expected_by'] = utcnow() + timedelta(minutes=body.expected_minutes)
    registry = _tasks(request)
    task = await registry.spawn(
        brain.agent_id,
        body.title,
        body.prompt,
        location=TaskLocation(body.location),
        model=body.model or agent.model_worker,
        origin=TaskOrigin.marcel,
        **fields,
    )
    return JSONResponse(registry.to_api(task), status_code=201)


@router.get('/tasks')
def list_tasks(
    request: Request,
    brain: BrainDep,
    state: str | None = None,
    project: str | None = None,
    archived: bool = False,
) -> Any:
    states: list[TaskState] = []
    for name in (state or '').split(','):
        if not name.strip():
            continue
        try:
            states.append(TaskState(name.strip()))
        except ValueError:
            return bad_request(f'state must be one of {", ".join(s.value for s in TaskState)}.')
    project_id = None
    if project is not None:
        found = _project_ref(request, brain, project)
        if isinstance(found, JSONResponse):
            return found
        project_id = found.id
    registry = _tasks(request)
    tasks, more = registry.list_tasks(brain.agent_id, states, archived, 0, LIST_LIMIT, project_id)
    return {'items': [registry.to_api(t) for t in tasks], 'more': more is not None}


@router.get('/tasks/{task_id}')
def get_task(request: Request, brain: BrainDep, task_id: str) -> Any:
    task = _own_task(request, brain, task_id)
    if task is None:
        return _task_not_found(task_id)
    registry = _tasks(request)
    body = registry.to_api(task)
    events, _ = registry.events(task_id, max(0, _last_seq(request, task_id) - 10), 10)
    body['recent_events'] = [registry.event_to_api(e) for e in events]
    return body


def _last_seq(request: Request, task_id: str) -> int:
    with Session(request.app.state.engine) as s:
        return (
            s.exec(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id)).one() or 0
        )


@router.post('/tasks/{task_id}/messages')
async def steer_task(request: Request, brain: BrainDep, task_id: str) -> Any:
    body = await _body(request, Steer, required=True)
    if isinstance(body, JSONResponse):
        return body
    if _own_task(request, brain, task_id) is None:
        return _task_not_found(task_id)
    registry = _tasks(request)
    try:
        task = await registry.steer(task_id, body.text, author='marcel')
    except TaskFinished as exc:
        return error(409, 'task_finished', str(exc))
    return registry.to_api(task)


@router.post('/tasks/{task_id}/stop')
async def stop_task(request: Request, brain: BrainDep, task_id: str) -> Any:
    if _own_task(request, brain, task_id) is None:
        return _task_not_found(task_id)
    registry = _tasks(request)
    try:
        task = await registry.stop(task_id, by='Marcel')
    except RunnerUnavailable as exc:
        return error(503, 'runner_unreachable', f'The task was not stopped. {exc}')
    return registry.to_api(task)


@router.post('/tasks/{task_id}/move')
async def move_task(request: Request, brain: BrainDep, task_id: str) -> Any:
    body = await _body(request, Move, required=True)
    if isinstance(body, JSONResponse):
        return body
    if _own_task(request, brain, task_id) is None:
        return _task_not_found(task_id)
    project = _live_project(request, brain, body.project)
    if isinstance(project, JSONResponse):
        return project
    try:
        _projects(request).move(task_id, project.id, brain.owner_user_id)
    except ProjectArchived as exc:  # pragma: no cover - archived between the two reads
        return error(409, 'project_archived', str(exc))
    registry = _tasks(request)
    return registry.to_api(registry.get(task_id))


# Projects -----------------------------------------------------------------------------------


class NewProject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60)
    slug: str | None = Field(default=None, pattern=SLUG)
    goal: str | None = Field(default=None, max_length=300)
    repo: str | None = Field(default=None, pattern=REPO)
    folder: str | None = Field(default=None, min_length=1)
    instructions: str | None = Field(default=None, max_length=MAX_TEXT)
    claude_project_url: AnyUrl | None = None


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=60)
    goal: str | None = Field(default=None, max_length=300)
    repo: str | None = Field(default=None, pattern=REPO)
    folder: str | None = Field(default=None, min_length=1)
    instructions: str | None = Field(default=None, max_length=MAX_TEXT)
    claude_project_url: AnyUrl | None = None


class BoardNote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=500)


def _fields(model: BaseModel) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, AnyUrl) else v
            for k, v in model.model_dump(exclude_unset=True).items()}  # fmt: skip


@router.get('/projects')
def list_projects(request: Request, brain: BrainDep, archived: bool = False) -> Any:
    return _projects(request).list_projects(brain.agent_id, brain.owner_user_id, archived)


@router.post('/projects')
async def create_project(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewProject, required=True)
    if isinstance(body, JSONResponse):
        return body
    fields = _fields(body)
    name, slug = fields.pop('name'), fields.pop('slug', None)
    store = _projects(request)
    try:
        project = store.create(
            brain.owner_user_id, brain.agent_id, name, slug, by='agent', **fields
        )
    except SlugTaken as exc:
        return error(
            409, 'slug_taken', f'A live project is already called “{exc}”. Pick another slug.'
        )
    return JSONResponse(store.to_api(project), status_code=201)


@router.patch('/projects/{project}')
async def update_project(request: Request, brain: BrainDep, project: str) -> Any:
    body = await _body(request, ProjectUpdate, required=True)
    if isinstance(body, JSONResponse):
        return body
    changes = {k: v for k, v in _fields(body).items() if v is not None}
    if not changes:
        return bad_request('Send at least one field to change.')
    found = _project_ref(request, brain, project)
    if isinstance(found, JSONResponse):
        return found
    store = _projects(request)
    return store.to_api(store.update(found.id, brain.owner_user_id, changes))


@router.post('/projects/{project}/notes')
async def add_board_note(request: Request, brain: BrainDep, project: str) -> Any:
    body = await _body(request, BoardNote, required=True)
    if isinstance(body, JSONResponse):
        return body
    found = _project_ref(request, brain, project)
    if isinstance(found, JSONResponse):
        return found
    try:
        return _ports(request).boards.add_note(found.id, body.text)
    except NotSetUp as exc:
        return not_set_up(exc)


# Artifacts ----------------------------------------------------------------------------------


class NewArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['pr', 'branch', 'doc', 'file', 'dashboard', 'link']
    title: str = Field(min_length=1, max_length=300)
    url: str | None = None
    path: str | None = None
    project: str | None = Field(default=None, min_length=1)
    artifact_id: str | None = Field(default=None, min_length=1)
    summary: str | None = Field(default=None, max_length=200)


@router.post('/artifacts')
async def publish_artifact(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewArtifact, required=True)
    if isinstance(body, JSONResponse):
        return body
    project = _live_project(request, brain, body.project)
    if isinstance(project, JSONResponse):
        return project
    spec = body.model_dump(exclude_none=True, exclude={'project'})
    try:
        art = _ports(request).artifacts.publish(spec, project.id, project.slug == INBOX_SLUG)
    except NotSetUp as exc:
        return not_set_up(exc)
    except ArtifactRefused as exc:
        return bad_request(str(exc))
    return JSONResponse(art, status_code=201)


# Memory -------------------------------------------------------------------------------------


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1)
    content: str = Field(max_length=MAX_FILE_BYTES)
    etag: str | None = Field(default=None, min_length=1)


def _memory(request: Request) -> MemoryRepo:
    return request.app.state.memory


def _bad_path(path: str) -> JSONResponse:
    return error(
        400,
        'invalid_memory_path',
        f'“{path}” is not a memory file. Use a relative path of letters, digits, “-”, “_” and “/” '
        'ending in .md, such as MEMORY.md or projects/garden.md.',
    )


@router.get('/memory')
def read_memory(request: Request, brain: BrainDep, path: str | None = None) -> Any:
    repo = _memory(request)
    try:
        if path is None:
            return {'files': repo.tree()}
        return repo.read(path)
    except InvalidMemoryPath:
        return _bad_path(path or '')
    except MemoryFileNotFound:
        return error(404, 'memory_file_not_found', f'There is no memory file {path}.')
    except MemoryFileTooBig:
        return error(400, 'memory_file_too_big', f'{path} is too big to read here.')
    except MemoryUnavailable as exc:
        return error(503, 'memory_unavailable', f'The memory cannot be read: {exc}')


@router.put('/memory')
async def write_memory(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, MemoryWrite, required=True)
    if isinstance(body, JSONResponse):
        return body
    if len(body.content.encode('utf-8')) > MAX_FILE_BYTES:
        return bad_request('The file is too long: memory files stay under 1 MB.')
    repo = _memory(request)
    try:
        # Off the event loop: the save runs git and holds the write lock.
        return await run_in_threadpool(repo.write, body.path, body.content, body.etag, 'agent')
    except InvalidMemoryPath:
        return _bad_path(body.path)
    except MemoryConflict as exc:
        current = exc.current
        return error(
            409,
            'memory_conflict',
            f'{body.path} changed since you read it (or exists already). Its etag is now '
            f'{current["etag"]}. Read it again, merge your change, and write with that etag.',
        )
    except MemoryFileNotFound:
        return error(
            404,
            'memory_file_not_found',
            f'{body.path} was removed since you read it. Write it without an etag to create it.',
        )
    except MemoryFileTooBig:
        return error(400, 'memory_file_too_big', f'{body.path} on disk is too big to replace here.')
    except MemoryUnavailable as exc:
        return error(503, 'memory_unavailable', f'Nothing was saved: {exc}')


# Schedules ----------------------------------------------------------------------------------


class NewSchedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    cron: str = Field(min_length=1, max_length=120)
    tz: str = Field(min_length=1)
    location: Literal['brain', 'nuc', 'cloud']
    prompt: str = Field(min_length=1, max_length=MAX_TEXT)
    deadline_minutes: int | None = Field(default=None, ge=1)


@router.post('/schedules')
async def create_schedule(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewSchedule, required=True)
    if isinstance(body, JSONResponse):
        return body
    if len(body.cron.split()) != 5:
        return bad_request(
            f'cron "{body.cron}" needs five fields: minute hour day month weekday, '
            'e.g. "0 8 * * 1" for Mondays at 08:00.'
        )
    try:
        ZoneInfo(body.tz)
    except (ZoneInfoNotFoundError, ValueError):
        return bad_request(f'tz "{body.tz}" is not a time zone. Use one like Europe/Brussels.')
    try:
        created = _ports(request).schedules.create(
            brain.agent_id, body.model_dump(exclude_none=True)
        )
    except NotSetUp as exc:
        return not_set_up(exc)
    return JSONResponse(created, status_code=201)


@router.get('/schedules')
def list_schedules(request: Request, brain: BrainDep) -> Any:
    try:
        return _ports(request).schedules.list(brain.agent_id)
    except NotSetUp as exc:
        return not_set_up(exc)


@router.delete('/schedules/{schedule_id}')
def delete_schedule(request: Request, brain: BrainDep, schedule_id: str) -> Any:
    try:
        _ports(request).schedules.delete(brain.agent_id, schedule_id)
    except NotSetUp as exc:
        return not_set_up(exc)
    except LookupError:
        return error(404, 'schedule_not_found', f'There is no schedule {schedule_id}.')
    except PermissionError as exc:
        return error(409, 'schedule_builtin', str(exc))
    return {'deleted': schedule_id}


# Notify and usage ---------------------------------------------------------------------------


class Notify(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    level: Literal['normal', 'urgent'] = 'normal'


@router.post('/notify')
async def notify(request: Request, brain: BrainDep) -> Any:
    """Post `text` in the main conversation and push it to the owner's phones. `urgent` is for
    something broken (category `broke`); `normal` for news that can wait (category `digest`)."""
    body = await _body(request, Notify, required=True)
    if isinstance(body, JSONResponse):
        return body
    engine = request.app.state.engine
    with Session(engine) as s:
        agent = s.get(Agent, brain.agent_id)
        assert agent is not None
        main = ensure_main_conversation(s, agent)
        message = post_message(
            s,
            request.app.state.events,
            main,
            role=MessageRole.agent,
            kind=MessageKind.text,
            body={'text': body.text},
        )
        message_id, name = message.id, agent.name
    push = PushNotification(
        title=clip(name, TITLE_MAX),
        body=clip(body.text, BODY_MAX),
        category='broke' if body.level == 'urgent' else 'digest',
        level=body.level,
        deep_link=message_link(message_id),
        collapse_key=message_id,  # each notice is its own: never held back as a repeat
    )
    pusher = getattr(request.app.state, 'pusher', None)
    pushed = bool(pusher.push(push, brain.owner_user_id)) if pusher is not None else False
    return {'message_id': message_id, 'pushed': pushed}


@router.get('/usage')
def get_usage(request: Request, brain: BrainDep) -> Any:
    try:
        return _ports(request).usage.current()
    except NotSetUp as exc:
        return not_set_up(exc)


# Wiring -------------------------------------------------------------------------------------


def install(app: FastAPI) -> BrainPorts:
    """Mount `/brain` and put the default ports on `app.state.brain_ports`. Call after the
    channel gateway, tasks, artifacts, memory and projects are installed."""
    ports = BrainPorts(artifacts=ReportedArtifacts(getattr(app.state, 'artifacts', None)))
    app.state.brain_ports = ports
    app.include_router(router)
    return ports


__all__ = [
    'Boards',
    'Brain',
    'BrainArtifacts',
    'BrainPorts',
    'NotSetUp',
    'Schedules',
    'Usage',
    'install',
    'require_brain',
]
