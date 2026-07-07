"""Scenario-based tests for jobs/scheduler.py.

Covers: rebuild_schedule emptiness guarantee, _resolve_stuck_runs,
_consolidate_memories, scheduler state persistence, schedule_job edge
cases, _handle_event, _dispatch, the oneshot lifecycle, and the three
long-running loops (tick, event, cleanup).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
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
    users = kw.pop('users', [user])
    return JobDefinition(
        name=kw.pop('name', 'test'),
        users=users,
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
        scheduler.schedule_job(job)

        reloaded = load_job(job.id)
        assert reloaded is not None
        assert reloaded.schedule_errors == 0

    def test_event_trigger_not_scheduled(self):
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        job = _make_job(trigger_type=TriggerType.EVENT)
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
        append_run(job.id, 'alice', stuck)

        _resolve_stuck_runs()

        runs = read_runs(job.id, 'alice')
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
        append_run(job.id, 'alice', done)

        _resolve_stuck_runs()
        runs = read_runs(job.id, 'alice')
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
# rebuild_schedule — no hardcoded bootstrap (regression for ISSUE-13c7f2)
# ---------------------------------------------------------------------------


class TestRebuildScheduleEmptiness:
    """After banking migrated to the zoo habitat (ISSUE-13c7f2), the kernel
    must not create any jobs from user state — the only sources of jobs are
    user-authored ``JOB.md`` files and the ``scheduled_jobs:`` habitat hook.
    """

    async def test_no_habitats_no_creds_means_zero_jobs(self, tmp_path, monkeypatch):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import JobScheduler

        # Empty zoo → no habitats registered
        monkeypatch.setenv('MARCEL_ZOO_DIR', str(tmp_path / 'empty-zoo'))

        # User with banking creds — the old _ensure_default_jobs would have
        # created a Bank sync job. Now it must be a no-op.
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('ENABLEBANKING_APP_ID=app123\nENABLEBANKING_SESSION_ID=sess456\n')

        scheduler = JobScheduler()
        await scheduler.rebuild_schedule()

        assert list_all_jobs() == []
        assert scheduler._schedule == {}


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

        reloaded = load_job(job.id)
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

    def test_stop_without_start_is_safe(self):
        """Stopping a scheduler that never started must not raise — the task
        slots are still None and there is nothing to cancel."""
        from marcel_core.jobs.scheduler import JobScheduler

        scheduler = JobScheduler()
        scheduler.stop()
        assert scheduler._tick_task is None


# ---------------------------------------------------------------------------
# _tick_loop
# ---------------------------------------------------------------------------


class TestTickLoop:
    @pytest.mark.asyncio
    async def test_dispatches_due_jobs_clears_stuck_and_heartbeats(self, monkeypatch, caplog):
        """One live pass through the tick loop: startup delay + rebuild, the
        stuck-job sweep, dispatch of a due job, and the periodic heartbeat."""
        from marcel_core.jobs import scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, '_STARTUP_DELAY', 0)
        monkeypatch.setattr(scheduler_module, '_TICK_INTERVAL', 0)

        sched = scheduler_module.JobScheduler()
        dispatched: list[str] = []

        async def fake_dispatch(job_id: str, trigger_reason: str = 'scheduled') -> None:
            dispatched.append(job_id)
            sched._schedule.pop(job_id, None)

        async def fake_rebuild() -> None:
            sched._schedule['due-1'] = datetime.now(UTC) - timedelta(seconds=1)

        sched._dispatch = fake_dispatch
        sched.rebuild_schedule = fake_rebuild

        # A job stuck since before the threshold — the first tick must clear it.
        stale = datetime.now(UTC) - timedelta(seconds=scheduler_module._STUCK_THRESHOLD + 60)
        sched._running.add('stuck-1')
        sched._running_since['stuck-1'] = stale

        with caplog.at_level(logging.INFO, logger='marcel_core.jobs.scheduler'):
            task = asyncio.create_task(sched._tick_loop())
            for _ in range(300):
                if dispatched and 'stuck-1' not in sched._running and 'Scheduler heartbeat' in caplog.text:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert 'due-1' in dispatched
        assert 'stuck-1' not in sched._running
        assert 'stuck-1' not in sched._running_since
        assert 'Clearing stuck job stuck-1' in caplog.text
        assert 'Scheduler heartbeat' in caplog.text

    @pytest.mark.asyncio
    async def test_restarts_on_crash_then_gives_up(self, monkeypatch, caplog):
        """A crashing rebuild trips the auto-restart ladder (with backoff) until
        the restart budget is exhausted, then the loop logs and exits."""
        from marcel_core.jobs import scheduler as scheduler_module

        real_sleep = asyncio.sleep

        async def instant_sleep(delay, *args, **kwargs):
            await real_sleep(0)

        monkeypatch.setattr(scheduler_module.asyncio, 'sleep', instant_sleep)

        sched = scheduler_module.JobScheduler()

        async def broken_rebuild() -> None:
            raise RuntimeError('rebuild exploded')

        sched.rebuild_schedule = broken_rebuild

        with caplog.at_level(logging.WARNING, logger='marcel_core.jobs.scheduler'):
            await asyncio.wait_for(sched._tick_loop(), timeout=5)

        assert 'Scheduler tick loop restarting' in caplog.text
        assert 'exhausted' in caplog.text


# ---------------------------------------------------------------------------
# _event_loop
# ---------------------------------------------------------------------------


class TestEventLoop:
    @pytest.mark.asyncio
    async def test_processes_queued_events(self):
        from marcel_core.jobs.scheduler import JobScheduler

        sched = JobScheduler()
        handled = asyncio.Event()
        events: list[tuple[str, str, str]] = []

        async def fake_handle(user_slug: str, job_id: str, status: str) -> None:
            events.append((user_slug, job_id, status))
            handled.set()

        sched._handle_event = fake_handle  # type: ignore[method-assign]

        task = asyncio.create_task(sched._event_loop())
        await sched.emit_event('alice', 'job-1', 'completed')
        await asyncio.wait_for(handled.wait(), timeout=2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert events == [('alice', 'job-1', 'completed')]

    @pytest.mark.asyncio
    async def test_crash_is_logged(self, caplog):
        from marcel_core.jobs.scheduler import JobScheduler

        sched = JobScheduler()

        async def broken_handle(user_slug: str, job_id: str, status: str) -> None:
            raise RuntimeError('handler exploded')

        sched._handle_event = broken_handle  # type: ignore[method-assign]

        task = asyncio.create_task(sched._event_loop())
        await sched.emit_event('alice', 'job-1', 'completed')
        with caplog.at_level(logging.ERROR, logger='marcel_core.jobs.scheduler'):
            await asyncio.wait_for(task, timeout=2)

        assert 'Scheduler event loop crashed' in caplog.text


# ---------------------------------------------------------------------------
# _handle_event filters
# ---------------------------------------------------------------------------


class TestHandleEventFilters:
    @pytest.mark.asyncio
    async def test_skips_inactive_event_jobs(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        source = _make_job(name='source')
        save_job(source)

        dependent = _make_job(
            name='dependent',
            trigger_type=TriggerType.EVENT,
            after_job=source.id,
            status=JobStatus.DISABLED,
        )
        save_job(dependent)

        scheduler = JobScheduler()
        with patch.object(scheduler, '_dispatch', new_callable=AsyncMock) as mock_dispatch:
            await scheduler._handle_event('alice', source.id, 'completed')
        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_jobs_chained_to_a_different_source(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import JobScheduler

        source = _make_job(name='source')
        save_job(source)

        other_dependent = _make_job(
            name='other-dependent',
            trigger_type=TriggerType.EVENT,
            after_job='some-other-job-id',
        )
        save_job(other_dependent)

        scheduler = JobScheduler()
        with patch.object(scheduler, '_dispatch', new_callable=AsyncMock) as mock_dispatch:
            await scheduler._handle_event('alice', source.id, 'completed')
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# _save_state failure path
# ---------------------------------------------------------------------------


class TestSaveStateFailure:
    def test_unwritable_state_path_is_logged_not_raised(self, tmp_path, caplog):
        from marcel_core.jobs.scheduler import JobScheduler

        # A directory squatting on the state file path makes write_text fail.
        (tmp_path / 'scheduler_state.json').mkdir()

        scheduler = JobScheduler()
        scheduler._schedule = {'job-a': datetime.now(UTC)}
        with caplog.at_level(logging.ERROR, logger='marcel_core.jobs.scheduler'):
            scheduler._save_state()  # must not raise

        assert 'Failed to persist scheduler state' in caplog.text


# ---------------------------------------------------------------------------
# _cleanup_loop
# ---------------------------------------------------------------------------


class TestCleanupLoop:
    @pytest.mark.asyncio
    async def test_single_pass_prunes_logs_and_consolidates(self, monkeypatch, caplog):
        """One cleanup pass: retention<=0 jobs are skipped, removed runs are
        logged, a crashing cleanup is contained, and memory consolidation runs."""
        import marcel_core.jobs as jobs_module
        from marcel_core.jobs import save_job, scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, '_CLEANUP_INTERVAL', 0)

        keep_forever = _make_job(name='keep-forever', retention_days=0)
        prunable = _make_job(name='prunable')
        broken = _make_job(name='broken')
        quiet = _make_job(name='quiet')  # retention>0 but nothing to remove
        for job in (keep_forever, prunable, broken, quiet):
            save_job(job)

        cleaned: list[str] = []

        def fake_cleanup(job_id: str, retention_days: int) -> int:
            cleaned.append(job_id)
            if job_id == broken.id:
                raise RuntimeError('cleanup exploded')
            return 3 if job_id == prunable.id else 0

        monkeypatch.setattr(jobs_module, 'cleanup_old_runs', fake_cleanup)

        consolidated = asyncio.Event()
        monkeypatch.setattr(scheduler_module, '_consolidate_memories', consolidated.set)

        sched = scheduler_module.JobScheduler()
        with caplog.at_level(logging.INFO, logger='marcel_core.jobs.scheduler'):
            task = asyncio.create_task(sched._cleanup_loop())
            await asyncio.wait_for(consolidated.wait(), timeout=2)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert keep_forever.id not in cleaned  # retention_days=0 → skipped
        assert prunable.id in cleaned
        assert quiet.id in cleaned  # visited, returned 0 → no "Cleaned" log for it
        assert f'Cleaned 3 old runs for job {prunable.id}' in caplog.text
        assert f'Cleaned 0 old runs for job {quiet.id}' not in caplog.text
        assert 'Cleanup failed for job' in caplog.text


# ---------------------------------------------------------------------------
# _consolidate_memories — per-user walk
# ---------------------------------------------------------------------------


class TestConsolidateMemoriesWalk:
    def test_prunes_skips_backups_and_contains_failures(self, tmp_path, monkeypatch, caplog):
        import marcel_core.storage.memory as memory_module
        from marcel_core.jobs.scheduler import _consolidate_memories

        users = tmp_path / 'users'
        (users / 'alice' / 'memory').mkdir(parents=True)
        (users / 'carol' / 'memory').mkdir(parents=True)
        (users / 'bob.backup-260101-120000').mkdir(parents=True)
        (users / 'stray.txt').write_text('not a user dir', encoding='utf-8')

        pruned_for: list[str] = []

        def fake_prune(slug: str) -> list[str]:
            pruned_for.append(slug)
            return ['expired.md'] if slug == 'alice' else []

        def fake_rebuild(slug: str) -> None:
            if slug == 'carol':
                raise RuntimeError('index rebuild failed')

        monkeypatch.setattr(memory_module, 'prune_expired_memories', fake_prune)
        monkeypatch.setattr(memory_module, 'rebuild_memory_index', fake_rebuild)
        monkeypatch.setattr(memory_module, 'enforce_index_cap', lambda slug: False)

        with caplog.at_level(logging.INFO, logger='marcel_core.jobs.scheduler'):
            _consolidate_memories()

        assert set(pruned_for) == {'alice', 'carol'}  # backup + file skipped
        assert 'pruned 1 expired memories for user=alice' in caplog.text
        assert 'Memory consolidation failed for user=carol' in caplog.text


# ---------------------------------------------------------------------------
# _latest_run_across_users
# ---------------------------------------------------------------------------


class TestLatestRunAcrossUsers:
    def test_picks_most_recent_run_across_user_logs(self):
        from marcel_core.jobs import append_run, save_job
        from marcel_core.jobs.scheduler import _latest_run_across_users

        job = _make_job(users=['alice', 'bob'])
        save_job(job)

        earlier = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(hours=2),
            finished_at=datetime.now(UTC) - timedelta(hours=2),
            output='alice-run',
        )
        later = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            finished_at=datetime.now(UTC) - timedelta(hours=1),
            output='bob-run',
        )
        append_run(job.id, 'alice', earlier)
        append_run(job.id, 'bob', later)

        latest = _latest_run_across_users(job)
        assert latest is not None
        assert latest.output == 'bob-run'

    def test_keeps_earlier_when_later_user_run_is_older(self):
        """Exercises the 'not more recent' branch: the first user's run is the
        latest, and a subsequent user's older run does not displace it."""
        from marcel_core.jobs import append_run, save_job
        from marcel_core.jobs.scheduler import _latest_run_across_users

        job = _make_job(users=['alice', 'bob'])
        save_job(job)

        newest = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            finished_at=datetime.now(UTC) - timedelta(minutes=5),
            output='alice-newest',
        )
        older = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(hours=3),
            finished_at=datetime.now(UTC) - timedelta(hours=3),
            output='bob-older',
        )
        append_run(job.id, 'alice', newest)
        append_run(job.id, 'bob', older)

        latest = _latest_run_across_users(job)
        assert latest is not None
        assert latest.output == 'alice-newest'

    def test_returns_none_when_never_run(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.scheduler import _latest_run_across_users

        job = _make_job(users=['alice', 'bob'])
        save_job(job)
        assert _latest_run_across_users(job) is None
