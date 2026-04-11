"""Job scheduler — manages cron, interval, and event-triggered job execution.

Runs as an asyncio task in the FastAPI lifespan, similar to the existing
banking sync loop. On startup, loads all active jobs for all users and
builds an in-memory schedule.

Architecture:
- Single asyncio.Task running a tick loop (every 30s)
- Per-job next_run_at computed from cron/interval + last run time
- Event triggers handled via an async event bus (asyncio.Queue)
- Scheduler state persisted to survive restarts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from marcel_core.jobs.models import JobDefinition, JobStatus, TriggerType

log = logging.getLogger(__name__)

_TICK_INTERVAL = 30  # seconds between schedule checks
_STARTUP_DELAY = 15  # seconds to wait before first tick
_STUCK_THRESHOLD = 7200  # seconds before a running job is considered stuck (2 hours)
_MAX_CONCURRENT = 3  # max parallel job executions
_STAGGER_WINDOW = 60  # seconds across which to spread jobs with identical schedules
_CATCHUP_MAX = 3  # max missed jobs to run immediately on startup
_CATCHUP_STAGGER = 30  # seconds between catchup dispatches
_CLEANUP_INTERVAL = 86400  # run cleanup once per day


def _stagger_offset(job_id: str, window: int = _STAGGER_WINDOW) -> int:
    """Deterministic per-job offset to spread jobs across a time window.

    Uses SHA-256 of the job ID so the same job always gets the same offset.
    """
    h = hashlib.sha256(job_id.encode()).digest()
    return int.from_bytes(h[:4], 'big') % window


def _compute_next_run(
    job: JobDefinition,
    last_run_at: datetime | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Compute the next run time for a job based on its trigger type.

    Returns None for event-triggered jobs (they fire on events, not schedules).
    """
    if now is None:
        now = datetime.now(UTC)

    if job.trigger.type == TriggerType.CRON:
        if not job.trigger.cron:
            return None
        from croniter import croniter

        # If a timezone is set, compute the cron schedule in local time
        # then convert back to UTC for the scheduler
        if job.trigger.timezone:
            tz = ZoneInfo(job.trigger.timezone)
            local_now = now.astimezone(tz)
            local_base = (last_run_at or now).astimezone(tz)
            cron = croniter(job.trigger.cron, local_base)
            next_local: datetime = cron.get_next(datetime)
            while next_local <= local_now:
                next_local = cron.get_next(datetime)
            # Convert back to UTC
            next_dt: datetime = next_local.astimezone(UTC)
        else:
            base = last_run_at or now
            cron = croniter(job.trigger.cron, base)
            next_dt = cron.get_next(datetime)
            # If computed next is in the past (e.g. after restart), advance to next future occurrence
            while next_dt <= now:
                next_dt = cron.get_next(datetime)
        # Deterministic stagger to avoid thundering herd
        return next_dt + timedelta(seconds=_stagger_offset(job.id))

    if job.trigger.type == TriggerType.INTERVAL:
        if not job.trigger.interval_seconds:
            return None
        base = last_run_at or now
        next_dt = base + timedelta(seconds=job.trigger.interval_seconds)
        # If we missed runs during downtime, schedule immediately
        if next_dt <= now:
            return now + timedelta(seconds=5)
        # Deterministic stagger to avoid thundering herd
        return next_dt + timedelta(seconds=_stagger_offset(job.id))

    if job.trigger.type == TriggerType.ONESHOT:
        if job.trigger.run_at:
            return job.trigger.run_at if job.trigger.run_at > now else now
        # No specific time — run immediately
        return now

    # Event triggers don't have scheduled times
    return None


def _ensure_default_jobs() -> None:
    """Create default jobs for users that have the required integrations.

    Currently creates a bank-sync job for every user with EnableBanking
    credentials, replacing the old hardcoded sync loop.
    """
    from marcel_core.jobs import list_jobs, save_job
    from marcel_core.jobs.models import JobDefinition, NotifyPolicy, TriggerSpec
    from marcel_core.storage._root import data_root
    from marcel_core.storage.credentials import load_credentials

    users_dir = data_root() / 'users'
    if not users_dir.is_dir():
        return

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        creds = load_credentials(slug)
        has_banking = bool(
            creds.get('ENABLEBANKING_APP_ID')
            and (creds.get('ENABLEBANKING_SESSIONS') or creds.get('ENABLEBANKING_SESSION_ID'))
        )
        if not has_banking:
            continue

        # Check if a bank-sync job already exists for this user
        existing = list_jobs(slug)
        if any(j.template == 'sync' and 'banking' in j.task.lower() for j in existing):
            continue

        job = JobDefinition(
            name='Bank sync',
            description='Sync bank transactions and balances every 8 hours',
            user_slug=slug,
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=8 * 60 * 60),
            system_prompt=(
                'You are a background sync worker for Marcel. '
                'Call the banking.sync integration to sync transactions and balances. '
                'Then call banking.balance to check all account balances. '
                'Report a brief summary. If any warnings were returned, include them.'
            ),
            task='Run banking.sync to sync all linked bank accounts. Report the results.',
            model='claude-haiku-4-5-20251001',
            skills=['banking.sync', 'banking.balance'],
            notify=NotifyPolicy.ON_FAILURE,
            channel='telegram',
            template='sync',
        )
        save_job(job)
        log.info('Created default bank-sync job %s for user %s', job.id, slug)


class JobScheduler:
    """Manages the scheduling and dispatch of background jobs."""

    def __init__(self) -> None:
        self._schedule: dict[str, datetime] = {}  # job_id -> next_run_at
        self._running: set[str] = set()  # job_ids currently executing
        self._running_since: dict[str, datetime] = {}  # job_id -> when dispatch started
        self._tick_task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    def start(self) -> None:
        """Start the scheduler loop, event listener, and cleanup task."""
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._event_task = asyncio.create_task(self._event_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info('Job scheduler started')

    def stop(self) -> None:
        """Cancel scheduler tasks."""
        for task in (self._tick_task, self._event_task, self._cleanup_task):
            if task:
                task.cancel()
        self._tick_task = None
        self._event_task = None
        self._cleanup_task = None
        log.info('Job scheduler stopped')

    async def emit_event(self, user_slug: str, job_id: str, status: str) -> None:
        """Called by executor after a job completes.  Triggers event-based jobs."""
        await self._event_queue.put((user_slug, job_id, status))

    def schedule_job(self, job: JobDefinition) -> None:
        """Add or update a job in the schedule.

        If computing the next run time fails 3 times cumulatively, the job is
        automatically disabled to prevent infinite error loops.
        """
        from marcel_core.jobs import last_run, save_job

        if job.status != JobStatus.ACTIVE:
            self._schedule.pop(job.id, None)
            return

        last = last_run(job.user_slug, job.id)
        last_run_at = last.finished_at if last else None

        try:
            next_at = _compute_next_run(job, last_run_at)
        except Exception:
            log.exception('Failed to compute next run for job %s (%s)', job.id, job.name)
            job.schedule_errors += 1
            if job.schedule_errors >= 3:
                log.warning('Auto-disabling job %s after %d schedule errors', job.id, job.schedule_errors)
                job.status = JobStatus.DISABLED
            save_job(job)
            self._schedule.pop(job.id, None)
            return

        # Reset schedule error counter on success
        if job.schedule_errors > 0:
            job.schedule_errors = 0
            save_job(job)

        if next_at:
            self._schedule[job.id] = next_at
            log.info('Scheduled job %s (%s) next run at %s', job.id, job.name, next_at.isoformat())
        else:
            self._schedule.pop(job.id, None)

    def unschedule_job(self, job_id: str) -> None:
        """Remove a job from the schedule."""
        self._schedule.pop(job_id, None)

    async def rebuild_schedule(self) -> None:
        """Load all active jobs from disk and rebuild the schedule.

        On startup this also:
        - Resolves orphaned RUNNING records from a previous crash
        - Loads saved state to detect missed jobs and staggers catchup
        """
        from marcel_core.jobs import list_all_jobs

        _ensure_default_jobs()

        # Clean up orphaned RUNNING records from previous process
        _resolve_stuck_runs()

        # Load saved state so we can detect missed jobs
        saved_state = self._load_state()

        self._schedule.clear()
        jobs = list_all_jobs()
        for job in jobs:
            self.schedule_job(job)

        # Startup catchup: identify jobs that were overdue according to saved state
        now = datetime.now(UTC)
        overdue: list[str] = []
        for job_id, saved_time_str in saved_state.items():
            try:
                saved_time = datetime.fromisoformat(saved_time_str)
                if saved_time < now and job_id in self._schedule:
                    overdue.append(job_id)
            except (ValueError, TypeError):
                continue

        # Stagger the first few catchup jobs, skip the rest (they get normal next run)
        for i, job_id in enumerate(overdue[:_CATCHUP_MAX]):
            self._schedule[job_id] = now + timedelta(seconds=i * _CATCHUP_STAGGER)
            log.info('Startup catchup: scheduling overdue job %s in %ds', job_id, i * _CATCHUP_STAGGER)

        log.info(
            'Schedule rebuilt: %d jobs scheduled (%d overdue catchup)',
            len(self._schedule),
            min(len(overdue), _CATCHUP_MAX),
        )
        self._save_state()

    async def _tick_loop(self) -> None:
        """Main scheduler loop.  Runs every ``_TICK_INTERVAL`` seconds."""
        try:
            await asyncio.sleep(_STARTUP_DELAY)
            await self.rebuild_schedule()

            while True:
                now = datetime.now(UTC)

                # Sweep for stuck jobs (running longer than threshold)
                stuck = [
                    jid
                    for jid, since in self._running_since.items()
                    if (now - since).total_seconds() > _STUCK_THRESHOLD
                ]
                for jid in stuck:
                    log.warning('Clearing stuck job %s (running since %s)', jid, self._running_since[jid].isoformat())
                    self._running.discard(jid)
                    self._running_since.pop(jid, None)

                due = [jid for jid, t in self._schedule.items() if t <= now and jid not in self._running]

                for job_id in due:
                    asyncio.create_task(self._dispatch(job_id))

                await asyncio.sleep(_TICK_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception('Scheduler tick loop crashed')

    async def _event_loop(self) -> None:
        """Listen for job completion events and trigger dependent jobs."""
        try:
            while True:
                user_slug, completed_job_id, status = await self._event_queue.get()
                await self._handle_event(user_slug, completed_job_id, status)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception('Scheduler event loop crashed')

    async def _handle_event(self, user_slug: str, completed_job_id: str, status: str) -> None:
        """Find and dispatch jobs triggered by the completion of another job."""
        from marcel_core.jobs import list_jobs

        jobs = list_jobs(user_slug)
        for job in jobs:
            if job.status != JobStatus.ACTIVE:
                continue
            if job.trigger.type != TriggerType.EVENT:
                continue
            if job.trigger.after_job != completed_job_id:
                continue
            # Check status filter
            if job.trigger.only_if_status and status != job.trigger.only_if_status.value:
                continue
            log.info(
                'Event trigger: job %s (%s) triggered by %s (%s)',
                job.id,
                job.name,
                completed_job_id,
                status,
            )
            asyncio.create_task(self._dispatch(job.id, trigger_reason=f'event:{completed_job_id}'))

    async def _dispatch(self, job_id: str, trigger_reason: str = 'scheduled') -> None:
        """Execute a job via the executor, update schedule after.

        Respects the concurrency semaphore — at most ``_MAX_CONCURRENT`` jobs
        run simultaneously.
        """
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.executor import execute_job_with_retries

        # Find the job across all users
        job: JobDefinition | None = None
        for j in list_all_jobs():
            if j.id == job_id:
                job = j
                break

        if not job or job.status != JobStatus.ACTIVE:
            self._schedule.pop(job_id, None)
            return

        self._running.add(job_id)
        self._running_since[job_id] = datetime.now(UTC)
        log.info('Dispatching job %s (%s) for user %s [%s]', job.id, job.name, job.user_slug, trigger_reason)

        try:
            async with self._semaphore:
                run = await execute_job_with_retries(job, trigger_reason)
            # Emit event for chained jobs
            await self.emit_event(job.user_slug, job.id, run.status.value)
        except Exception:
            log.exception('Job dispatch failed for %s', job_id)
        finally:
            self._running.discard(job_id)
            self._running_since.pop(job_id, None)

        # Handle oneshot: disable after first run
        if job.trigger.type == TriggerType.ONESHOT:
            from marcel_core.jobs import save_job

            job.status = JobStatus.DISABLED
            job.updated_at = datetime.now(UTC)
            save_job(job)
            self._schedule.pop(job_id, None)
        else:
            # Reschedule for next run
            self.schedule_job(job)

        self._save_state()

    def _save_state(self) -> None:
        """Persist scheduler state for restart recovery."""
        from marcel_core.storage._root import data_root

        state_path = data_root() / 'scheduler_state.json'
        state = {jid: t.isoformat() for jid, t in self._schedule.items()}
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
        except Exception:
            log.exception('Failed to persist scheduler state')

    def _load_state(self) -> dict[str, str]:
        """Load saved scheduler state from disk.  Returns ``{job_id: iso_time}``."""
        from marcel_core.storage._root import data_root

        state_path = data_root() / 'scheduler_state.json'
        if not state_path.exists():
            return {}
        try:
            return json.loads(state_path.read_text(encoding='utf-8'))
        except Exception:
            log.exception('Failed to load scheduler state')
            return {}

    async def _cleanup_loop(self) -> None:
        """Daily cleanup of old run records and memory consolidation."""
        try:
            while True:
                await asyncio.sleep(_CLEANUP_INTERVAL)
                from marcel_core.jobs import cleanup_old_runs, list_all_jobs

                for job in list_all_jobs():
                    if job.retention_days <= 0:
                        continue
                    try:
                        removed = cleanup_old_runs(job.user_slug, job.id, job.retention_days)
                        if removed:
                            log.info('Cleaned %d old runs for job %s', removed, job.id)
                    except Exception:
                        log.exception('Cleanup failed for job %s', job.id)

                # Memory consolidation: prune expired + rebuild index
                _consolidate_memories()
        except asyncio.CancelledError:
            pass


def _consolidate_memories() -> None:
    """Prune expired memories and rebuild the index for all users.

    Called daily by the cleanup loop. Removes schedule-type memories past
    their expiry date and enforces the index line cap.
    """
    from marcel_core.storage._root import data_root
    from marcel_core.storage.memory import (
        enforce_index_cap,
        prune_expired_memories,
        rebuild_memory_index,
    )

    users_dir = data_root() / 'users'
    if not users_dir.is_dir():
        return

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        try:
            pruned = prune_expired_memories(slug)
            if pruned:
                log.info('Memory consolidation: pruned %d expired memories for user=%s', len(pruned), slug)
            rebuild_memory_index(slug)
            enforce_index_cap(slug)
        except Exception:
            log.exception('Memory consolidation failed for user=%s', slug)


def _resolve_stuck_runs() -> None:
    """On startup, mark orphaned RUNNING records as FAILED.

    If the process crashed while a job was executing, the run record will be
    stuck with ``status=RUNNING`` and no ``finished_at``.  We append a
    corrected record so the run log is consistent.
    """
    from marcel_core.jobs import append_run, list_all_jobs, read_runs
    from marcel_core.jobs.models import JobRun, RunStatus

    for job in list_all_jobs():
        runs = read_runs(job.user_slug, job.id, limit=50)
        for run in runs:
            if run.status == RunStatus.RUNNING and run.finished_at is None:
                log.warning('Resolving stuck run %s for job %s', run.run_id, job.id)
                corrected = JobRun(
                    run_id=run.run_id,
                    job_id=run.job_id,
                    status=RunStatus.FAILED,
                    started_at=run.started_at,
                    finished_at=datetime.now(UTC),
                    error='Cleared: stuck after restart',
                    error_category='stuck',
                    trigger_reason=run.trigger_reason,
                    retry_count=run.retry_count,
                )
                append_run(job.user_slug, job.id, corrected)


# Module-level singleton
scheduler = JobScheduler()
