"""Data models for the Marcel job system.

Jobs are self-contained background tasks that run on schedules or in response
to events. Each job is stored as a directory under the user's data root with
a definition file (job.json) and an append-only run log (runs.jsonl).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class TriggerType(str, enum.Enum):
    """How a job is triggered."""

    CRON = 'cron'
    INTERVAL = 'interval'
    EVENT = 'event'
    ONESHOT = 'oneshot'


class JobStatus(str, enum.Enum):
    """Lifecycle status of a job definition."""

    ACTIVE = 'active'
    PAUSED = 'paused'
    DISABLED = 'disabled'


class RunStatus(str, enum.Enum):
    """Status of a single job execution."""

    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'


class NotifyPolicy(str, enum.Enum):
    """When to send the user a notification after a job run."""

    ALWAYS = 'always'
    ON_FAILURE = 'on_failure'
    ON_OUTPUT = 'on_output'
    SILENT = 'silent'


class TriggerSpec(BaseModel):
    """When and why a job runs."""

    type: TriggerType

    # cron trigger — standard cron expression (e.g. "0 7 * * *")
    cron: str | None = None

    # interval trigger — seconds between runs
    interval_seconds: int | None = None

    # event trigger — fire after another job completes
    after_job: str | None = None
    only_if_status: RunStatus | None = RunStatus.COMPLETED

    # oneshot trigger — optional scheduled time, or run immediately
    run_at: datetime | None = None


def _job_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(UTC)


class JobDefinition(BaseModel):
    """Complete definition of a background job.  Serialized to job.json."""

    id: str = Field(default_factory=_job_id)
    name: str
    description: str = ''
    user_slug: str
    status: JobStatus = JobStatus.ACTIVE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # Trigger
    trigger: TriggerSpec

    # Execution — the job's "brain"
    system_prompt: str
    task: str
    model: str = 'claude-haiku-4-5-20251001'

    # Skills the job needs — full docs + credentials are auto-injected into the prompt
    skills: list[str] = Field(default_factory=list)

    # pydantic-ai usage limits (None = pydantic-ai default of 50)
    request_limit: int | None = None

    # Notification
    notify: NotifyPolicy = NotifyPolicy.ON_OUTPUT
    channel: str = 'telegram'

    # Retry
    max_retries: int = 2
    retry_delay_seconds: int = 60

    # Template origin (for display/editing)
    template: str | None = None


def _run_id() -> str:
    return uuid.uuid4().hex[:8]


class JobRun(BaseModel):
    """Record of a single job execution.  Appended to runs.jsonl."""

    run_id: str = Field(default_factory=_run_id)
    job_id: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: str = ''
    error: str | None = None
    trigger_reason: str = ''
    retry_count: int = 0
