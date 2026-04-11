"""Tests for scheduler.rebuild_schedule and the tick/event/cleanup loops."""

from __future__ import annotations

import json
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


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


def _make_job(user: str = 'alice', trigger_type: TriggerType = TriggerType.INTERVAL, **kw) -> JobDefinition:
    trigger_kw: dict = {}
    if trigger_type == TriggerType.INTERVAL:
        trigger_kw['interval_seconds'] = kw.pop('interval_seconds', 3600)
    elif trigger_type == TriggerType.CRON:
        trigger_kw['cron'] = kw.pop('cron', '0 7 * * *')
    return JobDefinition(
        name=kw.pop('name', 'test'),
        user_slug=user,
        trigger=TriggerSpec(type=trigger_type, **trigger_kw),
        system_prompt='do stuff',
        task='run stuff',
        **kw,
    )


class TestRebuildSchedule:
    @pytest.mark.asyncio
    async def test_rebuild_loads_and_schedules_active_jobs(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        active = _make_job(name='active-job')
        save_job(active)

        paused = _make_job(name='paused-job', status=JobStatus.PAUSED)
        save_job(paused)

        scheduler = JobScheduler()
        with patch.object(scheduler, '_save_state'):
            await scheduler.rebuild_schedule()

        assert active.id in scheduler._schedule
        assert paused.id not in scheduler._schedule

    @pytest.mark.asyncio
    async def test_rebuild_handles_overdue_catchup(self, tmp_path):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job()
        save_job(job)

        # Save state with overdue time
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        state = {job.id: past}
        (tmp_path / 'scheduler_state.json').write_text(json.dumps(state))

        scheduler = JobScheduler()
        with patch.object(scheduler, '_save_state'):
            await scheduler.rebuild_schedule()

        # Overdue job should be rescheduled near-immediately
        assert job.id in scheduler._schedule
        now = datetime.now(UTC)
        assert scheduler._schedule[job.id] <= now + timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_rebuild_skips_invalid_saved_state(self, tmp_path):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job()
        save_job(job)

        # Invalid state: bad dates
        state = {job.id: 'not-a-date', 'ghost': '2026-01-01T00:00:00+00:00'}
        (tmp_path / 'scheduler_state.json').write_text(json.dumps(state))

        scheduler = JobScheduler()
        with patch.object(scheduler, '_save_state'):
            await scheduler.rebuild_schedule()  # should not raise


class TestDispatchExceptionHandling:
    @pytest.mark.asyncio
    async def test_dispatch_handles_executor_exception(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        job = _make_job()
        save_job(job)

        scheduler = JobScheduler()

        with patch(
            'marcel_core.jobs.executor.execute_job_with_retries',
            new_callable=AsyncMock,
            side_effect=RuntimeError('executor crash'),
        ):
            await scheduler._dispatch(job.id)

        # Should have cleaned up running state
        assert job.id not in scheduler._running


class TestCleanupLoop:
    @pytest.mark.asyncio
    async def test_cleanup_removes_old_runs_for_active_jobs(self, tmp_path):
        from marcel_core.jobs import append_run, cleanup_old_runs, save_job

        job = _make_job(retention_days=7)
        save_job(job)

        # Add old run
        old_run = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(days=10),
            finished_at=datetime.now(UTC) - timedelta(days=10),
        )
        append_run('alice', job.id, old_run)

        removed = cleanup_old_runs('alice', job.id, 7)
        assert removed == 1


class TestSaveState:
    def test_save_state_creates_file(self, tmp_path):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        now = datetime.now(UTC)
        scheduler._schedule = {'j1': now}
        scheduler._save_state()

        state_path = tmp_path / 'scheduler_state.json'
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert 'j1' in data
