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
  worker reports through the channel and never starts or stops other work. One exception: a
  rollover's new brain, before its hello hands it the conversation, may make the reads in
  `READ_BEFORE_HELLO` (its `SessionStart` hook asks for the open threads); see
  `BrainKeeper.incoming`.
- **Its own agent only.** Every task, project and schedule is looked up within the caller's agent.
  Anything else answers 404, exactly like something that does not exist.

Errors answer `{code, message}`; the message is written for the brain to act on.

Seams for later stories
-----------------------
Some tools reach parts of the hub that are built elsewhere. Each sits behind a small interface on
`app.state.brain_ports`; until its story plugs in the real one, the default answers 503
`not_set_up` with a plain sentence, and nothing is stored:

- `boards` (`board_note`): project boards; the app plugs in `boards.BrainBoards` (S-04.11).
- `artifacts` (`artifacts_publish`): the default stores through the artifact store's
  `publish_reported` (the same path as a worker's `report`), filed under the project the brain
  named (its agent's Inbox by default). With `artifact_id` it adds a new version of one of that
  project's items (S-04.11); an item of another project, or a board, is refused.

Every request body is read only up to a cap (`MAX_BODY_BYTES`, more for a memory write), and
every text field has a maximum length.
- `digests` (`artifacts_publish` with `in_reply_to`): which `digest_id`s the hub knows, from the
  digests (`digest.Digests`, S-04.14), plugged in by `digest.install`. Until then every id is
  unknown: 404 `digest_not_found`, and nothing is stored.
- `schedules` (`schedule_*`): the scheduler (`schedule.Scheduler`, S-09.1), plugged in by
  `schedule.install`.
- `usage` (`usage_get`): the usage guard (`usage.UsageGuard`, S-10.1), plugged in by
  `usage.install` when the hub has a runner.
"""

import contextvars
import hmac
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated, Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, AnyUrl, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import event, func
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from marcel_hub.artifacts import ArtifactRefused, ArtifactStore
from marcel_hub.auth import ApiError
from marcel_hub.channel.gateway import CHAT_ROLES, MIN_SECRET, Gateway
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.day_summary import day_summary
from marcel_hub.memory import (
    MAX_FILE_BYTES,
    AgentMemory,
    InvalidMemoryPath,
    MemoryConflict,
    MemoryFileNotFound,
    MemoryFileTooBig,
    MemoryRepo,
    MemoryUnavailable,
)
from marcel_hub.models import (
    Agent,
    Artifact,
    ArtifactVersion,
    Message,
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
from marcel_hub.projects.store import ProjectArchived, ProjectStore, SlugTaken
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.tasks.registry import TaskFinished, TaskNotFound, TaskRegistry
from marcel_hub.tasks.routes import bad_request, error

log = logging.getLogger(__name__)

TUNNEL_HEADER = 'cf-connecting-ip'
SECRET_HEADER = 'x-marcel-secret'
SESSION_HEADER = 'x-marcel-session'
SLUG = r'^[a-z0-9][a-z0-9-]{0,39}$'
MODEL = r'^[A-Za-z0-9._\[\]][A-Za-z0-9._\[\]-]{0,79}$'  # runner-api.yaml's
# owner/name, as GitHub allows: no leading '-' (never read as a flag), no '..' (never a path).
REPO = r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$'


def _no_dot_dot(value: str) -> str:
    if '..' in value:
        raise ValueError('a repo is owner/name, without ".."')
    return value


Repo = Annotated[str, Field(pattern=REPO), AfterValidator(_no_dot_dot)]
MAX_TEXT = 20000
MAX_REF = 80  # a project slug or id, a task, message, artifact or schedule id
MAX_URL = 2048
MAX_PATH = 1024
# A request body is read no further than this; a memory write may carry a whole memory file.
MAX_BODY_BYTES = 64 * 1024
MAX_MEMORY_BODY_BYTES = 2 * MAX_FILE_BYTES + 4096
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


# What a rollover's new brain may call before its hello hands it the conversation: reads only.
# A new GET route is not on this list until someone decides it is safe (test_brain_startup pins it).
READ_BEFORE_HELLO = frozenset(
    {
        '/brain/tasks',
        '/brain/tasks/{task_id}',
        '/brain/projects',
        '/brain/memory',
        '/brain/schedules',
        '/brain/summary',
        '/brain/usage',
    }
)


async def require_brain(request: Request) -> Brain:
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
    keeper = getattr(request.app.state, 'brain', None)
    if peer is None and session_id and keeper is not None and await keeper.claim(session_id):
        peer = gateway.resolve(session_id)  # a fresh brain that called before its spawn returned
    if peer is None and session_id and keeper is not None:
        early = await keeper.incoming(session_id)  # a rollover's brain before its hand-over
        if early is not None:
            route = getattr(request.scope.get('route'), 'path', '')
            if request.method != 'GET' or route not in READ_BEFORE_HELLO:
                raise ApiError(
                    401,
                    'unknown_session',
                    'This session takes over as Marcel’s brain at its hello; until then it can '
                    'only read.',
                )
            agent_id, conversation_id = early
            with Session(request.app.state.engine) as s:
                agent = s.get(Agent, agent_id)
            if agent is None:  # pragma: no cover - a foreign key
                raise _hidden()
            return Brain(session_id, 'brain', agent.id, agent.owner_user_id, conversation_id)
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
    def publish(
        self, spec: dict[str, Any], project_id: str, in_reply_to: str | None = None
    ) -> dict[str, Any]:
        """Store an artifact Marcel hands in (channel.schema.json `Artifact`), filed under
        `project_id`, with the `digest_id` it answers kept on the new version. Returns the
        app-api `Artifact`. Raises `ArtifactRefused`."""
        ...


class DigestIds(Protocol):
    def knows(self, agent_id: str, digest_id: str) -> bool:
        """Whether `digest_id` names a `digest_due` event the hub sent this agent's brain."""
        ...


class NoDigests:
    def knows(self, agent_id: str, digest_id: str) -> bool:
        return False


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


# The project a brain artifact is being filed under, while `ReportedArtifacts.publish` runs. The
# store has no project argument, so the row is filed on insert (before the models' own filing,
# which would put an artifact without a task in the first agent's Inbox).
_filing: contextvars.ContextVar[str | None] = contextvars.ContextVar('brain_filing', default=None)


@event.listens_for(Artifact, 'before_insert', insert=True)
def _file_brain_artifact(_mapper: Any, _connection: Any, art: Artifact) -> None:
    project_id = _filing.get()
    if project_id is not None and art.project_id is None and art.task_id is None:
        art.project_id = project_id


class ReportedArtifacts:
    """Marcel's artifacts through the artifact store, the same way a worker's are stored, filed
    under the project the brain named."""

    def __init__(self, store: ArtifactStore | None) -> None:
        self.store = store

    def publish(
        self, spec: dict[str, Any], project_id: str, in_reply_to: str | None = None
    ) -> dict[str, Any]:
        if self.store is None:
            raise NotSetUp('This hub has no artifact store, so nothing was saved.')
        token = _filing.set(project_id)
        try:
            art = self.store.publish_reported(spec, project_id=project_id, author='Marcel')
        finally:
            _filing.reset(token)
        if in_reply_to is not None:
            with Session(self.store.engine) as s:
                version = s.get(ArtifactVersion, (art.id, art.current_version))
                assert version is not None
                version.in_reply_to = in_reply_to
                s.add(version)
                s.commit()
        return self.store.to_api(art)


@dataclass
class BrainPorts:
    artifacts: BrainArtifacts
    boards: Boards = field(default_factory=NoBoards)
    schedules: Schedules = field(default_factory=NoSchedules)
    usage: Usage = field(default_factory=NoUsage)
    digests: DigestIds = field(default_factory=NoDigests)


# Helpers ------------------------------------------------------------------------------------


def _ports(request: Request) -> BrainPorts:
    return request.app.state.brain_ports


def _tasks(request: Request) -> TaskRegistry:
    return request.app.state.tasks


def _projects(request: Request) -> ProjectStore:
    return request.app.state.projects


async def _body[M: BaseModel](
    request: Request, model: type[M], limit: int = MAX_BODY_BYTES
) -> M | JSONResponse:
    """The JSON body as `model`, read no further than `limit` bytes."""
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        return _too_large(limit)
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            return _too_large(limit)
    if not raw.strip():
        return bad_request('The request needs a JSON body.')
    try:
        return model.model_validate_json(bytes(raw))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = '.'.join(str(p) for p in first['loc']) or 'body'
        return bad_request(f'{where}: {first["msg"]}.')


def _too_large(limit: int) -> JSONResponse:
    return error(413, 'too_large', f'The request is too big: keep it under {limit // 1024} KB.')


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
    if len(ref) > MAX_REF:
        return bad_request(f'A project is named by its slug or id, at most {MAX_REF} characters.')
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
    project: str | None = Field(default=None, min_length=1, max_length=MAX_REF)
    repo: Repo | None = None
    model: str | None = Field(default=None, pattern=MODEL)
    expected_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    message_id: str | None = Field(default=None, min_length=1, max_length=MAX_REF)


class Steer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class Move(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project: str = Field(min_length=1, max_length=MAX_REF)


@router.post('/tasks')
async def spawn_task(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewTask)
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
        asked = s.get(Message, body.message_id) if body.message_id is not None else None
    assert agent is not None
    if body.message_id is not None and (
        asked is None or asked.conversation_id != brain.conversation_id
    ):
        return error(
            404,
            'message_not_found',
            f'There is no message {body.message_id} in this conversation. Leave out message_id, '
            'or use the message_id of the owner’s message you are answering.',
        )
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
    body = await _body(request, Steer)
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
    body = await _body(request, Move)
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
    repo: Repo | None = None
    folder: str | None = Field(default=None, min_length=1, max_length=MAX_PATH)
    instructions: str | None = Field(default=None, max_length=MAX_TEXT)
    claude_project_url: AnyUrl | None = Field(default=None, max_length=MAX_URL)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=60)
    goal: str | None = Field(default=None, max_length=300)
    repo: Repo | None = None
    folder: str | None = Field(default=None, min_length=1, max_length=MAX_PATH)
    instructions: str | None = Field(default=None, max_length=MAX_TEXT)
    claude_project_url: AnyUrl | None = Field(default=None, max_length=MAX_URL)


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
    body = await _body(request, NewProject)
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
    body = await _body(request, ProjectUpdate)
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
    body = await _body(request, BoardNote)
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
    url: str | None = Field(default=None, max_length=MAX_URL)
    path: str | None = Field(default=None, max_length=MAX_PATH)
    project: str | None = Field(default=None, min_length=1, max_length=MAX_REF)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=MAX_REF)
    summary: str | None = Field(default=None, max_length=200)
    in_reply_to: str | None = Field(default=None, min_length=1, max_length=MAX_REF)


@router.post('/artifacts')
async def publish_artifact(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewArtifact)
    if isinstance(body, JSONResponse):
        return body
    project = _live_project(request, brain, body.project)
    if isinstance(project, JSONResponse):
        return project
    ports = _ports(request)
    if body.in_reply_to is not None and not ports.digests.knows(brain.agent_id, body.in_reply_to):
        return error(
            404,
            'digest_not_found',
            f'There is no digest {body.in_reply_to} to answer. Use the digest_id of the '
            'digest_due event, or leave in_reply_to out.',
        )
    spec = body.model_dump(exclude_none=True, exclude={'project', 'in_reply_to'})
    try:
        art = ports.artifacts.publish(spec, project.id, body.in_reply_to)
    except NotSetUp as exc:
        return not_set_up(exc)
    except ArtifactRefused as exc:
        return bad_request(str(exc))
    return JSONResponse(art, status_code=201)


# Memory -------------------------------------------------------------------------------------


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=MAX_PATH)
    content: str = Field(max_length=MAX_FILE_BYTES)
    etag: str | None = Field(default=None, min_length=1, max_length=64)


def _memory(request: Request, brain: Brain) -> AgentMemory:
    """The brain's own agent's memory, never another person's (S-17.4, B-38)."""
    repo: MemoryRepo = request.app.state.memory
    return repo.for_agent(brain.agent_id)


def _bad_path(path: str) -> JSONResponse:
    return error(
        400,
        'invalid_memory_path',
        f'“{path}” is not a memory file. Use a relative path of letters, digits, “-”, “_” and “/” '
        'ending in .md, such as MEMORY.md or projects/garden.md.',
    )


@router.get('/memory')
def read_memory(request: Request, brain: BrainDep, path: str | None = None) -> Any:
    if path is not None and len(path) > MAX_PATH:
        return _bad_path(path[:40] + '…')
    repo = _memory(request, brain)
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
    body = await _body(request, MemoryWrite, MAX_MEMORY_BODY_BYTES)
    if isinstance(body, JSONResponse):
        return body
    if len(body.content.encode('utf-8')) > MAX_FILE_BYTES:
        return bad_request('The file is too long: memory files stay under 1 MB.')
    repo = _memory(request, brain)
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
    tz: str = Field(min_length=1, max_length=64)
    location: Literal['brain', 'nuc', 'cloud']
    prompt: str = Field(min_length=1, max_length=MAX_TEXT)
    deadline_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)


@router.post('/schedules')
async def create_schedule(request: Request, brain: BrainDep) -> Any:
    body = await _body(request, NewSchedule)
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
    except ValueError as exc:  # a cron field out of range, or a day that never comes
        return bad_request(str(exc))
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
    """Post `text` in the main conversation and push it to the owner's phones as Marcel's own
    notice (category `note`; `level` decides how loud). At most 6 pushes per hour per agent; past
    that the message is still posted, the push is dropped and logged, and `pushed` is false.
    `broke` stays for the hub's own system faults."""
    body = await _body(request, Notify)
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
    pusher = getattr(request.app.state, 'pusher', None)
    pushed = (
        bool(
            pusher.note(
                brain.agent_id, message_id, name, body.text, body.level, brain.owner_user_id
            )
        )
        if pusher is not None
        else False
    )
    return {'message_id': message_id, 'pushed': pushed}


@router.get('/usage')
def get_usage(request: Request, brain: BrainDep) -> Any:
    try:
        return _ports(request).usage.current()
    except NotSetUp as exc:
        return not_set_up(exc)


@router.get('/summary')
def get_summary(request: Request, brain: BrainDep) -> dict[str, str]:
    """`DaySummary`: the day so far, for the brain after a compaction (`day_summary.py`)."""
    with Session(request.app.state.engine) as s:
        agent = s.get(Agent, brain.agent_id)
        assert agent is not None  # require_brain found it
        today, text = day_summary(s, agent, ZoneInfo(request.app.state.settings.tz), utcnow())
    return {'date': today.isoformat(), 'text': text}


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
    'DigestIds',
    'NotSetUp',
    'Schedules',
    'Usage',
    'install',
    'require_brain',
]
