"""SQLModel tables for the data model in plan/02-architecture.md.

Ids are strings with a prefix (`tsk_…`), as in the contract. Timestamps are timezone-aware UTC.
Enum members are named like their values so the database holds what the API says.
"""

import secrets
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
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
    client_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('tsk'), primary_key=True)
    agent_id: str = Field(foreign_key='agent.id', index=True)
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


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id('art'), primary_key=True)
    task_id: str | None = Field(default=None, foreign_key='task.id', index=True)
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
