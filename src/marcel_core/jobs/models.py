"""Data models for the Marcel job system.

Jobs are self-contained background tasks that run on schedules or in response
to events. Each job lives at ``<data_root>/jobs/<slug>/`` as a SKILL.md-style
document: ``JOB.md`` (YAML frontmatter + ``## System Prompt`` / ``## Task``
body) carries the user-authored definition, ``state.json`` holds mutable
runtime state (errors, timestamps), and ``runs/<user>.jsonl`` (plus
``runs/_system.jsonl`` for system-scope jobs with ``users: []``) is the
append-only per-user run log.
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
    TIMED_OUT = 'timed_out'


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

    # timezone for cron expressions (e.g. "Europe/Brussels") — None means UTC
    timezone: str | None = None


def _job_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(UTC)


class JobDefinition(BaseModel):
    """Complete definition of a background job.

    Persisted to ``<data_root>/jobs/<slug>/JOB.md`` as a YAML frontmatter +
    markdown body document; mutable runtime state (``consecutive_errors``,
    ``last_error_at``, ``schedule_errors``, ``last_failure_alert_at``,
    ``updated_at``) is split into a sibling ``state.json`` so scheduler
    bookkeeping never clobbers hand-authored prompts.

    A job targets zero or more users via :attr:`users`. An empty list marks
    the job as system-scope — it runs once per tick without a user context
    (no per-user credentials, no memories, no auto-notify) and its run log
    is filed under the reserved ``_system`` slug.
    """

    id: str = Field(default_factory=_job_id)
    name: str
    description: str = ''
    users: list[str] = Field(default_factory=list)
    """Users this job runs for. Empty list = system-scope."""
    status: JobStatus = JobStatus.ACTIVE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # Trigger
    trigger: TriggerSpec

    # Execution — the job's "brain"
    system_prompt: str
    task: str
    model: str = 'anthropic:claude-haiku-4-5-20251001'

    # Skills the job needs — full docs + credentials are auto-injected into the prompt
    skills: list[str] = Field(default_factory=list)

    # pydantic-ai usage limits (None = pydantic-ai default of 50)
    request_limit: int | None = None

    # Opt-in: after cloud retries exhaust, try one final run against the
    # configured local LLM (ISSUE-070). Requires MARCEL_LOCAL_LLM_URL and
    # MARCEL_LOCAL_LLM_MODEL to be set in the environment.
    allow_local_fallback: bool = False

    # Whether this job participates in the global model fallback chain
    # (ISSUE-076). Default True — a failing cloud primary escalates to
    # MARCEL_BACKUP_MODEL after retries exhaust, then to
    # MARCEL_FALLBACK_MODEL in complete-mode if ``allow_local_fallback``
    # is also set. Set to False for:
    #
    # - deterministic jobs whose output must come from one specific model
    # - jobs deliberately pinned to a cheap model where escalation would
    #   blow up cost (e.g. a 5-minute cron job pinned to Haiku)
    # - jobs deliberately pinned to a local model (`local:<tag>`) — the
    #   chain would silently escalate to cloud and defeat the purpose.
    #   ALWAYS set this to False when also pinning to a local model.
    #
    # See docs/model-tiers.md for the full behaviour matrix.
    allow_fallback_chain: bool = True

    # Notification
    notify: NotifyPolicy = NotifyPolicy.ON_OUTPUT
    channel: str = 'telegram'

    # Retry
    max_retries: int = 2
    retry_delay_seconds: int = 60
    backoff_schedule: list[int] = Field(default_factory=lambda: [30, 60, 300, 900, 3600])

    # Timeout — kills the job agent if it exceeds this duration
    timeout_seconds: int = 600

    # Failure tracking (persisted across runs for alerting/backoff)
    consecutive_errors: int = 0
    last_error_at: datetime | None = None
    schedule_errors: int = 0

    # Failure alert cooldown (for ON_FAILURE notify policy)
    alert_after_consecutive_failures: int = 3
    alert_cooldown_seconds: int = 3600
    last_failure_alert_at: datetime | None = None

    # Housekeeping
    retention_days: int = 30

    # Template origin (for display/editing)
    template: str | None = None


def _run_id() -> str:
    return uuid.uuid4().hex[:8]


class JobRun(BaseModel):
    """Record of a single job execution.

    Appended to the per-user log at ``<data_root>/jobs/<slug>/runs/<user>.jsonl``
    (or ``runs/_system.jsonl`` for system-scope jobs).
    """

    run_id: str = Field(default_factory=_run_id)
    job_id: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: str = ''
    error: str | None = None
    error_category: str | None = None
    trigger_reason: str = ''
    retry_count: int = 0
    agent_notified: bool = False
    delivery_status: str | None = None
    delivery_error: str | None = None
    # Which fallback model the executor used on this run, if any.
    # Currently only ``"local"`` or None — set by the ISSUE-070 fallback path.
    fallback_used: str | None = None
