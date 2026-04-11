"""Scenario-based tests for jobs/scheduler.py.

Covers: _ensure_default_jobs, _resolve_stuck_runs, _consolidate_memories,
scheduler state persistence, schedule_job edge cases, _handle_event,
_dispatch, and the oneshot lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.jobs.models import (
    JobDefinition,
    JobRun,
    JobStatus,
    RunStatus,
    TriggerSpec,
    TriggerType,
)
from marcel_core.storage import _root


def _make_job(user: str = 'alice', trigger_type: TriggerType = TriggerType.INTERVAL, **kw) -> JobDefinition:
    trigger_kw: dict = {}
    if trigger_type == TriggerType.INTERVAL:
        trigger_kw['interval_seconds'] = kw.pop('interval_seconds', 3600)
    elif trigger_type == TriggerType.CRON:
        trigger_kw['cron'] = kw.pop('cron', '0 7 * * *')
    elif trigger_type == TriggerType.EVENT:
        trigger_kw['after_job'] = kw.pop('after_job', 'other-job')
    return JobDefinition(
        name=kw.pop('name', 'test'),
        user_slug=user,
        trigger=TriggerSpec(type=trigger_type, **trigger_kw),
        system_prompt='do stuff',
        task='run stuff',
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_save_and_load_state(self, tmp_path):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        now = datetime.now(UTC)
        scheduler._schedule = {'job-a': now, 'job-b': now + timedelta(hours=1)}
        scheduler._save_state()

        state = scheduler._load_state()
        assert 'job-a' in state
        assert 'job-b' in state

    def test_load_state_missing_file(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        assert scheduler._load_state() == {}

    def test_load_state_corrupt_file(self, tmp_path):
        from marcel_core.jobs.scheduler import JobScheduler

        (tmp_path / 'scheduler_state.json').write_text('not json')
        scheduler = JobScheduler()
        assert scheduler._load_state() == {}


# ---------------------------------------------------------------------------
# schedule_job edge cases
# ---------------------------------------------------------------------------


class TestScheduleJob:
    def test_inactive_job_removed_from_schedule(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        job = _make_job(status=JobStatus.PAUSED)
        scheduler._schedule[job.id] = datetime.now(UTC)
        scheduler.schedule_job(job)
        assert job.id not in scheduler._schedule

    def test_schedule_resets_error_counter(self):
        from marcel_core.jobs import load_job, save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job(schedule_errors=1)
        save_job(job)

        scheduler = JobScheduler()
        with patch('marcel_core.jobs.last_run', return_value=None):
            scheduler.schedule_job(job)

        reloaded = load_job('alice', job.id)
        assert reloaded is not None
        assert reloaded.schedule_errors == 0

    def test_event_trigger_not_scheduled(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        job = _make_job(trigger_type=TriggerType.EVENT)
        with patch('marcel_core.jobs.last_run', return_value=None):
            scheduler.schedule_job(job)
        assert job.id not in scheduler._schedule

    def test_unschedule_job(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        scheduler._schedule['abc'] = datetime.now(UTC)
        scheduler.unschedule_job('abc')
        assert 'abc' not in scheduler._schedule


# ---------------------------------------------------------------------------
# _compute_next_run edge cases
# ---------------------------------------------------------------------------


class TestComputeNextRunEdgeCases:
    def test_cron_no_expression(self):
        from marcel_core.jobs.scheduler import _compute_next_run

        job = _make_job(trigger_type=TriggerType.CRON, cron=None)
        job.trigger.cron = None
        assert _compute_next_run(job) is None

    def test_interval_no_seconds(self):
        from marcel_core.jobs.scheduler import _compute_next_run

        job = _make_job(trigger_type=TriggerType.INTERVAL)
        job.trigger.interval_seconds = None
        assert _compute_next_run(job) is None

    def test_interval_missed_during_downtime(self):
        from marcel_core.jobs.scheduler import _compute_next_run

        job = _make_job(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
        now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
        last_run = datetime(2026, 4, 11, 6, 0, tzinfo=UTC)  # 6h ago, interval is 1h
        result = _compute_next_run(job, last_run_at=last_run, now=now)
        # Should schedule near-immediately
        assert result is not None
        assert result <= now + timedelta(seconds=10)

    def test_oneshot_with_run_at_in_future(self):
        from marcel_core.jobs.scheduler import _compute_next_run

        future = datetime(2026, 12, 25, 0, 0, tzinfo=UTC)
        job = _make_job(trigger_type=TriggerType.ONESHOT)
        job.trigger.run_at = future
        now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
        result = _compute_next_run(job, now=now)
        assert result == future

    def test_oneshot_with_run_at_in_past(self):
        from marcel_core.jobs.scheduler import _compute_next_run

        past = datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        job = _make_job(trigger_type=TriggerType.ONESHOT)
        job.trigger.run_at = past
        now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
        result = _compute_next_run(job, now=now)
        assert result == now

    def test_cron_advances_past_now(self):
        """If the computed next run is in the past, advance until future."""
        from marcel_core.jobs.scheduler import _compute_next_run

        job = _make_job(trigger_type=TriggerType.CRON, cron='0 7 * * *')
        now = datetime(2026, 4, 11, 8, 0, tzinfo=UTC)  # 08:00, so 07:00 today is past
        last_run = datetime(2026, 4, 10, 7, 0, tzinfo=UTC)  # yesterday 07:00
        result = _compute_next_run(job, last_run_at=last_run, now=now)
        assert result is not None
        assert result > now


# ---------------------------------------------------------------------------
# _resolve_stuck_runs
# ---------------------------------------------------------------------------


class TestResolveStuckRuns:
    def test_marks_stuck_as_failed(self):
        from marcel_core.jobs import append_run, read_runs, save_job
        from marcel_core.jobs.scheduler import _resolve_stuck_runs

        job = _make_job()
        save_job(job)
        stuck = JobRun(
            job_id=job.id,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(hours=3),
        )
        append_run('alice', job.id, stuck)

        _resolve_stuck_runs()

        runs = read_runs('alice', job.id)
        # Should have original RUNNING + corrected FAILED
        failed_runs = [r for r in runs if r.status == RunStatus.FAILED]
        assert len(failed_runs) == 1
        assert failed_runs[0].error == 'Cleared: stuck after restart'

    def test_does_not_touch_completed(self):
        from marcel_core.jobs import append_run, read_runs, save_job
        from marcel_core.jobs.scheduler import _resolve_stuck_runs

        job = _make_job()
        save_job(job)
        done = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        append_run('alice', job.id, done)

        _resolve_stuck_runs()
        runs = read_runs('alice', job.id)
        assert len(runs) == 1
        assert runs[0].status == RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# _consolidate_memories
# ---------------------------------------------------------------------------


class TestConsolidateMemories:
    def test_runs_without_error_on_empty(self):
        from marcel_core.jobs.scheduler import _consolidate_memories

        # Should not raise when no users exist
        _consolidate_memories()

    def test_runs_for_user_with_memories(self, tmp_path):
        from marcel_core.jobs.scheduler import _consolidate_memories

        # Create a minimal user memory dir
        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('# Memory Index\n')
        (mem_dir / 'test.md').write_text('---\nname: test\ntype: fact\n---\nContent\n')

        _consolidate_memories()  # Should not raise


# ---------------------------------------------------------------------------
# _ensure_default_jobs
# ---------------------------------------------------------------------------


class TestEnsureDefaultJobs:
    def test_creates_bank_sync_for_banking_user(self, tmp_path):
        from marcel_core.jobs import list_jobs
        from marcel_core.jobs.scheduler import _ensure_default_jobs

        # Set up user with banking creds
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('ENABLEBANKING_APP_ID=app123\nENABLEBANKING_SESSION_ID=sess456\n')

        _ensure_default_jobs()

        jobs = list_jobs('alice')
        assert len(jobs) == 1
        assert jobs[0].template == 'sync'
        assert 'banking' in jobs[0].task.lower()

    def test_skips_user_without_banking(self, tmp_path):
        from marcel_core.jobs import list_jobs
        from marcel_core.jobs.scheduler import _ensure_default_jobs

        user_dir = tmp_path / 'users' / 'bob'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('')

        _ensure_default_jobs()
        assert list_jobs('bob') == []

    def test_does_not_duplicate(self, tmp_path):
        from marcel_core.jobs import list_jobs
        from marcel_core.jobs.scheduler import _ensure_default_jobs

        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('ENABLEBANKING_APP_ID=x\nENABLEBANKING_SESSION_ID=y\n')

        _ensure_default_jobs()
        _ensure_default_jobs()  # call again

        jobs = list_jobs('alice')
        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_nonexistent_job(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        scheduler._schedule['ghost'] = datetime.now(UTC)
        await scheduler._dispatch('ghost')
        assert 'ghost' not in scheduler._schedule

    @pytest.mark.asyncio
    async def test_dispatch_disabled_job(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job(status=JobStatus.DISABLED)
        save_job(job)

        scheduler = JobScheduler()
        scheduler._schedule[job.id] = datetime.now(UTC)
        await scheduler._dispatch(job.id)
        assert job.id not in scheduler._schedule

    @pytest.mark.asyncio
    async def test_dispatch_oneshot_disables_after_run(self):
        from marcel_core.jobs import load_job, save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job(trigger_type=TriggerType.ONESHOT)
        save_job(job)

        mock_run = JobRun(job_id=job.id, status=RunStatus.COMPLETED)
        scheduler = JobScheduler()

        with patch(
            'marcel_core.jobs.executor.execute_job_with_retries',
            new_callable=AsyncMock,
            return_value=mock_run,
        ):
            await scheduler._dispatch(job.id)

        reloaded = load_job('alice', job.id)
        assert reloaded is not None
        assert reloaded.status == JobStatus.DISABLED

    @pytest.mark.asyncio
    async def test_dispatch_reschedules_interval_job(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
        save_job(job)

        mock_run = JobRun(job_id=job.id, status=RunStatus.COMPLETED)
        scheduler = JobScheduler()

        with patch(
            'marcel_core.jobs.executor.execute_job_with_retries',
            new_callable=AsyncMock,
            return_value=mock_run,
        ):
            await scheduler._dispatch(job.id)

        # Should be rescheduled
        assert job.id in scheduler._schedule


# ---------------------------------------------------------------------------
# _handle_event
# ---------------------------------------------------------------------------


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_triggers_chained_job(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        # Create a source job and a dependent event-triggered job
        source = _make_job(name='source')
        save_job(source)

        dependent = _make_job(
            name='dependent',
            trigger_type=TriggerType.EVENT,
            after_job=source.id,
        )
        dependent.trigger.only_if_status = RunStatus.COMPLETED
        save_job(dependent)

        scheduler = JobScheduler()
        with patch.object(scheduler, '_dispatch', new_callable=AsyncMock) as mock_dispatch:
            await scheduler._handle_event('alice', source.id, 'completed')
        mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_trigger_on_wrong_status(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        source = _make_job(name='source')
        save_job(source)

        dependent = _make_job(
            name='dependent',
            trigger_type=TriggerType.EVENT,
            after_job=source.id,
        )
        dependent.trigger.only_if_status = RunStatus.COMPLETED
        save_job(dependent)

        scheduler = JobScheduler()
        with patch.object(scheduler, '_dispatch', new_callable=AsyncMock) as mock_dispatch:
            await scheduler._handle_event('alice', source.id, 'failed')
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# emit_event
# ---------------------------------------------------------------------------


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_puts_event_on_queue(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        await scheduler.emit_event('alice', 'job-1', 'completed')
        item = scheduler._event_queue.get_nowait()
        assert item == ('alice', 'job-1', 'completed')


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    def test_start_creates_tasks(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        with patch('asyncio.create_task') as mock_create:
            scheduler.start()
        assert mock_create.call_count == 3
        scheduler.stop()
        assert scheduler._tick_task is None
        assert scheduler._event_task is None
        assert scheduler._cleanup_task is None
