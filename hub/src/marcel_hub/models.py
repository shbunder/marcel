"""SQLModel tables for the data model in plan/02-architecture.md.

Ids are strings with a prefix (`tsk_…`), as in the contract. Timestamps are timezone-aware UTC.
Enum members are named like their values so the database holds what the API says.
"""

import secrets
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, Index, event, insert, select, text
from sqlalchemy.engine import Connection
from sqlmodel import Field, SQLModel


def new_id(prefix: str) -> str:
    """A sortable id: millisecond clock then random bytes, e.g. `tsk_019a2b3c4d5e1f2a3b4c`."""
    return f'{prefix}_{int(time.time() * 1000):011x}{secrets.token_hex(4)}'


def utcnow() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    owner = 'owner'
    member = 'member'


class ConversationKind(StrEnum):
    main = 'main'
    side = 'side'


class MessageRole(StrEnum):
    user = 'user'
    agent = 'agent'
    system = 'system'


class MessageKind(StrEnum):
    text = 'text'
    milestone = 'milestone'
    approval = 'approval'
    quote = 'quote'
    artifact = 'artifact'


class TaskState(StrEnum):
    queued = 'queued'
    starting = 'starting'
    working = 'working'
    needs_you = 'needs_you'
    done = 'done'
    failed = 'failed'
    stopped = 'stopped'
    silent = 'silent'
    unknown = 'unknown'


class TaskLocation(StrEnum):
    nuc = 'nuc'
    cloud = 'cloud'


class QueuedReason(StrEnum):
    cap = 'cap'
    usage = 'usage'


class TaskOrigin(StrEnum):
    marcel = 'marcel'
    adopted = 'adopted'
    schedule = 'schedule'


class TaskEventType(StrEnum):
    text = 'text'
    tool_call = 'tool_call'
    tool_result = 'tool_result'
    diff = 'diff'
    permission = 'permission'
    subagent_start = 'subagent_start'
    subagent_stop = 'subagent_stop'
    workflow_start = 'workflow_start'  # a workflow's run (S-04.16, app-api 0.7.3)
    workflow_stop = 'workflow_stop'
    workflow_agent_start = 'workflow_agent_start'
    workflow_agent_stop = 'workflow_agent_stop'
    error = 'error'
    raw = 'raw'
    state = 'state'
    progress = 'progress'
    artifact = 'artifact'
    note = 'note'


class ApprovalState(StrEnum):
    open = 'open'
    approved = 'approved'
    denied = 'denied'
    expired = 'expired'


class AnsweredVia(StrEnum):
    app = 'app'
    terminal = 'terminal'


class ArtifactKind(StrEnum):
    pr = 'pr'
    branch = 'branch'
    doc = 'doc'
    file = 'file'
    dashboard = 'dashboard'
    link = 'link'
    board = 'board'


class ScheduleLocation(StrEnum):
    brain = 'brain'
    nuc = 'nuc'
    cloud = 'cloud'


class UsageWindow(StrEnum):
    five_hour = 'five_hour'
    weekly = 'weekly'


def _json() -> Any:
    return Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class User(SQLModel, table=True):
    """A person who uses Marcel (B-37): the owner, or a family member the owner added. The owner
    keeps the id `usr_owner`; its row is made at start-up (S-17.1), not by the migration."""

    id: str = Field(default_factory=lambda: new_id('usr'), primary_key=True)
    name: str
    role: UserRole
    created_at: datetime = Field(default_factory=utcnow)


class Agent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('agt'), primary_key=True)
    owner_user_id: str = Field(index=True)
    name: str
    animal: str
    palette: str
    seed: str
    persona_path: str | None = None
    model_brain: str = 'opus'
    model_worker: str = 'sonnet'
    main_conversation_id: str | None = None
    # The Pixel Zoo look (B-44, ADR-008): the contract's `look` object, one column per part. None
    # until the agent is onboarded; the routes check the values against the contract's enums.
    look_animal: str | None = None
    look_color: str | None = None
    look_personality: str | None = None
    onboarded_at: datetime | None = None
    archived_at: datetime | None = None
    # Fixed when the agent is made and never changed by a rename (its workspace and memory folders
    # use it). Unique; the owner's agent from before 0.7 is `marcel` (migration 0010).
    slug: str | None = Field(default=None, unique=True, index=True)


class Device(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('dev'), primary_key=True)
    user_id: str = Field(index=True)
    name: str
    token_hash: str = Field(unique=True, index=True)
    apns_token: str | None = None
    ntfy_topic: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_seen: datetime | None = None


class PairingCode(SQLModel, table=True):
    """A one-time code from `marcel-hub pair`. Only its sha256 is stored; it works once, until
    `expires_at`."""

    __tablename__ = 'pairing_code'  # type: ignore[assignment]

    code_hash: str = Field(primary_key=True)
    user_id: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = None


INBOX_SLUG = 'inbox'


class Project(SQLModel, table=True):
    """A goal that groups threads (B-30). Every agent has one `inbox` project, made with the agent
    (see `_file_new_agents` below). A slug is unique among an agent's live projects; an archived
    project's slug can be used again.

    Its board (B-35) is an artifact of kind `board` with the id `board_artifact_id`, made with the
    project (`_make_board` below); the hub fills and rebuilds its content (`boards.py`)."""

    __table_args__ = (
        Index(
            'ix_project_live_slug',
            'agent_id',
            'slug',
            unique=True,
            sqlite_where=text('archived = 0'),
        ),
    )

    id: str = Field(default_factory=lambda: new_id('prj'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
    slug: str
    name: str
    goal: str | None = None
    repo: str | None = None
    folder: str | None = None
    instructions: str | None = None
    claude_project_url: str | None = None
    tracker_url: str | None = None  # B-46: the project's issue tracker, a link only
    board_artifact_id: str = Field(default_factory=lambda: new_id('art'))
    archived: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('cnv'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
    kind: ConversationKind
    parent_message_id: str | None = None
    task_id: str | None = Field(default=None, index=True)
    brain_session_id: str | None = None
    title: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('msg'), primary_key=True)
    conversation_id: str = Field(foreign_key='conversation.id', index=True)
    role: MessageRole
    kind: MessageKind
    body_json: dict[str, Any] = _json()
    task_id: str | None = Field(default=None, index=True)
    # The project a user message was sent from (B-32); the message stays in the main conversation.
    project_id: str | None = Field(default=None, foreign_key='project.id')
    # For `artifact`: the item that arrived (B-36).
    artifact_id: str | None = None
    client_id: str | None = Field(default=None, index=True)
    # B-41: the message this one answers ("reply to" in the app).
    reply_to_message_id: str | None = Field(default=None, foreign_key='message.id')
    # On an `agent` message, the agent that wrote it: its conversation's (app-api 0.7.2).
    agent_id: str | None = None
    # B-42: the contract's `mentions`, as a JSON list; empty when there are none.
    mentions_json: list[Any] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default='[]')
    )
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AppEvent(SQLModel, table=True):
    """One frame of the app WebSocket (`contracts/events.schema.json`). `id` counts up by one per
    user and survives a restart, so a phone that reconnects with `?since=` gets exactly what it
    missed. Only the latest events are kept; older ones are pruned as new ones arrive."""

    __tablename__ = 'app_event'  # type: ignore[assignment]

    user_id: str = Field(primary_key=True)
    id: int = Field(primary_key=True)
    type: str
    data_json: dict[str, Any] = _json()
    at: datetime = Field(default_factory=utcnow)


class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('tsk'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
    # Required: a task inserted without one is filed in its agent's inbox (`_file_in_inbox`), and
    # the database refuses a task with none (triggers in migration 0005).
    project_id: str | None = Field(default=None, foreign_key='project.id', index=True)
    title: str
    location: TaskLocation
    state: TaskState = Field(index=True)
    queued_reason: QueuedReason | None = None
    model: str
    session_id: str | None = None
    cloud_url: str | None = None
    remote_url: str | None = None
    repo: str | None = None
    branch: str | None = None
    worktree: str | None = None
    origin: TaskOrigin
    parent_message_id: str | None = None
    schedule_id: str | None = None
    summary: str | None = None
    waiting_for: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime | None = None
    expected_by: datetime | None = None
    archived_at: datetime | None = None


class TaskEvent(SQLModel, table=True):
    """Normalised transcript and hub events. `seq` counts up by one within a task."""

    __tablename__ = 'task_event'  # type: ignore[assignment]

    task_id: str = Field(foreign_key='task.id', primary_key=True)
    seq: int = Field(primary_key=True)
    type: TaskEventType
    data_json: dict[str, Any] = _json()
    at: datetime = Field(default_factory=utcnow)


class Approval(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('apr'), primary_key=True)
    task_id: str = Field(foreign_key='task.id', index=True)
    request_id: str | None = Field(default=None, index=True)
    tool: str
    summary: str
    detail: str | None = None
    artifact_id: str | None = None
    state: ApprovalState = ApprovalState.open
    answered_via: AnsweredVia | None = None
    created_at: datetime = Field(default_factory=utcnow)
    answered_at: datetime | None = None


class ChannelFrame(SQLModel, table=True):
    """A hub → session frame (channel.schema.json) waiting for its `ack`. `target` is
    `task:<id>` for a worker or `conversation:<id>` for the brain or a side thread. `seq` counts
    up by one per target and never restarts: the newest row of a target is kept even once acked,
    so numbering survives a restart."""

    __tablename__ = 'channel_frame'  # type: ignore[assignment]

    target: str = Field(primary_key=True)
    seq: int = Field(primary_key=True)
    frame_json: dict[str, Any] = _json()
    acked: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class ChannelCall(SQLModel, table=True):
    """A `reply` or `report` the hub has stored, by `call_id`, so a re-send is stored once."""

    __tablename__ = 'channel_call'  # type: ignore[assignment]

    call_id: str = Field(primary_key=True)
    target: str
    at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('art'), primary_key=True)
    task_id: str | None = Field(default=None, foreign_key='task.id', index=True)
    # Its task's project, or the inbox for an artifact with no task (`_file_artifact`).
    project_id: str | None = Field(default=None, foreign_key='project.id', index=True)
    kind: ArtifactKind
    title: str
    url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    trusted: bool = False
    self_change: bool = False
    meta_json: dict[str, Any] = _json()
    created_at: datetime = Field(default_factory=utcnow)
    # Living artifacts (B-34): the fields above describe the current version.
    current_version: int = 1
    updated_by: str | None = None  # in plain words: a task's title, "Marcel" or "the hub"
    updated_at: datetime = Field(default_factory=utcnow)
    # Moved to its project by the owner (`POST /api/artifacts/{id}/move`, migration 0007): a
    # pinned item stays where it is when its task moves; an unpinned one follows its task.
    pinned: bool = False


class ArtifactVersion(SQLModel, table=True):
    """One version of an artifact (B-34). Version 1 is made with the artifact. A file's bytes for
    each version are kept by the artifact store (`artifacts.store.blob_name`)."""

    __tablename__ = 'artifact_version'  # type: ignore[assignment]

    artifact_id: str = Field(foreign_key='artifact.id', primary_key=True)
    version: int = Field(primary_key=True)
    url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    summary: str | None = None
    author: str | None = None
    task_id: str | None = None
    # The `digest_id` of the `digest_due` event this version answers (brain-api `in_reply_to`).
    in_reply_to: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Schedule(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('sch'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
    name: str
    cron: str
    tz: str
    location: ScheduleLocation
    prompt: str
    paused: bool = False
    builtin: bool = False
    deadline_minutes: int | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class BrainRollover(SQLModel, table=True):
    """One brain rollover (S-06.7): the facts of its `brain.rollover` event, kept apart from the
    app event log (which keeps only the latest events) so the notebook can list past ones."""

    __tablename__ = 'brain_rollover'  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: new_id('rol'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    reason: str  # context_full | crashed | daily | manual
    kept: str
    old_session_id: str | None = None  # None: there was no brain before (never for a rollover)
    new_session_id: str


class UsageSnapshot(SQLModel, table=True):
    __tablename__ = 'usage_snapshot'  # type: ignore[assignment]

    id: str = Field(default_factory=lambda: new_id('usg'), primary_key=True)
    taken_at: datetime = Field(default_factory=utcnow, index=True)
    window: UsageWindow
    used_pct: float
    resets_at: datetime | None = None
    source: str


# Filing -------------------------------------------------------------------------------------
# These run inside the flush that writes the row, so whatever code inserts an agent, a task or an
# artifact, the inbox exists and the row is filed under a project.


def inbox_id(connection: Connection, agent_id: str) -> str:
    """The id of the agent's inbox project, made now if the agent has none."""
    table = Project.__table__  # type: ignore[attr-defined]
    found = connection.execute(
        select(table.c.id).where(table.c.agent_id == agent_id, table.c.slug == INBOX_SLUG)
    ).first()
    if found is not None:
        return found[0]
    now = utcnow()
    project_id = new_id('prj')
    board_id = new_id('art')
    connection.execute(
        insert(table).values(
            id=project_id,
            agent_id=agent_id,
            slug=INBOX_SLUG,
            name='Inbox',
            board_artifact_id=board_id,
            archived=False,
            created_at=now,
            updated_at=now,
        )
    )
    make_board(connection, project_id, board_id, 'Inbox')
    return project_id


BOARD_AUTHOR = 'the hub'


def board_title(project_name: str) -> str:
    return f'{project_name} — board'


def make_board(connection: Connection, project_id: str, board_id: str, name: str) -> None:
    """The project's board artifact at version 1, with no content yet: the hub's board keeper
    writes it (`boards.BoardKeeper`). Also used by migration 0006 for projects made before."""
    now = utcnow()
    connection.execute(
        insert(Artifact.__table__).values(  # type: ignore[attr-defined]
            id=board_id, project_id=project_id, kind=ArtifactKind.board.value,
            title=board_title(name), content_type='application/json', trusted=False,
            self_change=False, meta_json={}, created_at=now, current_version=1,
            updated_by=BOARD_AUTHOR, updated_at=now,
        )
    )  # fmt: skip
    connection.execute(
        insert(ArtifactVersion.__table__).values(  # type: ignore[attr-defined]
            artifact_id=board_id, version=1, content_type='application/json',
            author=BOARD_AUTHOR, created_at=now,
        )
    )  # fmt: skip


@event.listens_for(Agent, 'after_insert')
def _file_new_agents(_mapper: Any, connection: Connection, agent: Agent) -> None:
    inbox_id(connection, agent.id)


@event.listens_for(Project, 'after_insert')
def _make_board(_mapper: Any, connection: Connection, project: Project) -> None:
    make_board(connection, project.id, project.board_artifact_id, project.name)


@event.listens_for(Task, 'before_insert')
def _file_in_inbox(_mapper: Any, connection: Connection, task: Task) -> None:
    if task.project_id is None:
        task.project_id = inbox_id(connection, task.agent_id)


# auth.tokens.OWNER_USER_ID (auth/ imports models, so it is spelled out here once).
OWNER_ID = 'usr_owner'


@event.listens_for(Artifact, 'before_insert')
def _file_artifact(_mapper: Any, connection: Connection, art: Artifact) -> None:
    """An artifact belongs to its task's project. One with no task and no project goes to the
    inbox of the owner's first agent, never a family member's (B-38, S-17.2); with no such agent
    it stays unfiled. Its author
    (`updated_by`) is its task's title unless the caller named one."""
    if art.current_version == 1:
        art.updated_at = art.created_at
    tasks = Task.__table__  # type: ignore[attr-defined]
    if art.task_id is not None and (art.project_id is None or art.updated_by is None):
        row = connection.execute(
            select(tasks.c.project_id, tasks.c.title).where(tasks.c.id == art.task_id)
        ).first()
        if row is not None:
            art.project_id = art.project_id or row[0]
            art.updated_by = art.updated_by or row[1]
    if art.project_id is not None or art.task_id is not None:
        return
    agents = Agent.__table__  # type: ignore[attr-defined]
    first = connection.execute(
        select(agents.c.id).where(agents.c.owner_user_id == OWNER_ID).order_by(agents.c.id).limit(1)
    ).scalar()
    if first is not None:
        art.project_id = inbox_id(connection, first)


@event.listens_for(Artifact, 'after_insert')
def _first_version(_mapper: Any, connection: Connection, art: Artifact) -> None:
    """Every artifact has its version 1, whatever code inserts it."""
    connection.execute(
        insert(ArtifactVersion.__table__).values(  # type: ignore[attr-defined]
            artifact_id=art.id, version=art.current_version, url=art.url,
            content_type=art.content_type, size_bytes=art.size_bytes, author=art.updated_by,
            task_id=art.task_id, created_at=art.updated_at,
        )
    )  # fmt: skip
