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


class ScheduleLocation(StrEnum):
    brain = 'brain'
    nuc = 'nuc'
    cloud = 'cloud'


class UsageWindow(StrEnum):
    five_hour = 'five_hour'
    weekly = 'weekly'


def _json() -> Any:
    return Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


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

    `board_artifact_id` is reserved when the project is made; the board artifact itself is built
    under that id by S-04.11."""

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
    client_id: str | None = Field(default=None, index=True)
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
    connection.execute(
        insert(table).values(
            id=project_id,
            agent_id=agent_id,
            slug=INBOX_SLUG,
            name='Inbox',
            board_artifact_id=new_id('art'),
            archived=False,
            created_at=now,
            updated_at=now,
        )
    )
    return project_id


@event.listens_for(Agent, 'after_insert')
def _file_new_agents(_mapper: Any, connection: Connection, agent: Agent) -> None:
    inbox_id(connection, agent.id)


@event.listens_for(Task, 'before_insert')
def _file_in_inbox(_mapper: Any, connection: Connection, task: Task) -> None:
    if task.project_id is None:
        task.project_id = inbox_id(connection, task.agent_id)


@event.listens_for(Artifact, 'before_insert')
def _file_artifact(_mapper: Any, connection: Connection, art: Artifact) -> None:
    """An artifact belongs to its task's project. One with no task goes to the inbox of the
    hub's first agent (v1 has one owner); with no agent at all it stays unfiled."""
    if art.project_id is not None:
        return
    if art.task_id is not None:
        tasks = Task.__table__  # type: ignore[attr-defined]
        art.project_id = connection.execute(
            select(tasks.c.project_id).where(tasks.c.id == art.task_id)
        ).scalar()
        return
    agents = Agent.__table__  # type: ignore[attr-defined]
    first = connection.execute(select(agents.c.id).order_by(agents.c.id).limit(1)).scalar()
    if first is not None:
        art.project_id = inbox_id(connection, first)
