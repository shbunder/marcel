"""Tests for the job scheduler hardening features.

Covers: staggering, schedule error auto-disable, stuck detection, startup
catchup, and the compute_next_run helper.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from marcel_core.jobs.models import (
    JobDefinition,
    JobStatus,
    TriggerSpec,
    TriggerType,
)
from marcel_core.jobs.scheduler import (
    _STAGGER_WINDOW,
    _compute_next_run,
    _stagger_offset,
)

# ---------------------------------------------------------------------------
# _stagger_offset
# ---------------------------------------------------------------------------


class TestStaggerOffset:
    def test_deterministic(self):
        """Same job ID always produces the same offset."""
        assert _stagger_offset('abc123') == _stagger_offset('abc123')

    def test_within_window(self):
        """Offset is always in [0, window)."""
        for seed in ('a', 'b', 'c', 'job_999', ''):
            offset = _stagger_offset(seed, window=60)
            assert 0 <= offset < 60

    def test_different_ids_differ(self):
        """Different job IDs almost certainly get different offsets."""
        offsets = {_stagger_offset(f'job_{i}') for i in range(50)}
        assert len(offsets) > 1  # at least some variation


# ---------------------------------------------------------------------------
# _compute_next_run (stagger applied)
# ---------------------------------------------------------------------------


class TestComputeNextRun:
    def _make_job(self, trigger_type: TriggerType, **trigger_kw) -> JobDefinition:
        return JobDefinition(
            name='test',
            users=['test'],
            trigger=TriggerSpec(type=trigger_type, **trigger_kw),
            system_prompt='do stuff',
            task='do stuff',
        )

    def test_cron_includes_stagger(self):
        job = self._make_job(TriggerType.CRON, cron='0 7 * * *')
        now = datetime(2026, 4, 11, 6, 0, tzinfo=UTC)
        result = _compute_next_run(job, now=now)
        # Base next run would be 07:00, stagger adds [0, 60) seconds
        assert result is not None
        base = datetime(2026, 4, 11, 7, 0, tzinfo=UTC)
        assert result >= base
        assert result < base + timedelta(seconds=_STAGGER_WINDOW)

    def test_interval_includes_stagger(self):
        job = self._make_job(TriggerType.INTERVAL, interval_seconds=3600)
        now = datetime(2026, 4, 11, 10, 0, tzinfo=UTC)
        last_run = datetime(2026, 4, 11, 9, 0, tzinfo=UTC)
        result = _compute_next_run(job, last_run_at=last_run, now=now)
        assert result is not None
        base = last_run + timedelta(seconds=3600)
        assert result >= base
        assert result < base + timedelta(seconds=_STAGGER_WINDOW)

    def test_oneshot_no_stagger(self):
        """Oneshot jobs should not be staggered."""
        job = self._make_job(TriggerType.ONESHOT)
        now = datetime(2026, 4, 11, 10, 0, tzinfo=UTC)
        result = _compute_next_run(job, now=now)
        assert result == now

    def test_event_returns_none(self):
        job = self._make_job(TriggerType.EVENT, after_job='other')
        result = _compute_next_run(job)
        assert result is None

    def test_cron_with_timezone_converts_to_utc(self):
        """A cron with a timezone is evaluated in local time, then converted back
        to UTC. 07:00 Europe/Brussels in April (CEST, UTC+2) is 05:00 UTC."""
        job = self._make_job(TriggerType.CRON, cron='0 7 * * *', timezone='Europe/Brussels')
        now = datetime(2026, 4, 11, 1, 0, tzinfo=UTC)  # 03:00 local — 07:00 local still ahead
        result = _compute_next_run(job, now=now)
        assert result is not None
        base = datetime(2026, 4, 11, 5, 0, tzinfo=UTC)  # 07:00 CEST
        assert base <= result < base + timedelta(seconds=_STAGGER_WINDOW)

    def test_cron_with_timezone_advances_past_now(self):
        """If the local next-run computed from last_run_at is already in the past,
        the schedule advances to the next future local occurrence."""
        job = self._make_job(TriggerType.CRON, cron='0 7 * * *', timezone='Europe/Brussels')
        now = datetime(2026, 4, 11, 10, 0, tzinfo=UTC)  # 12:00 local — today's 07:00 has passed
        last_run = datetime(2026, 4, 10, 5, 0, tzinfo=UTC)  # yesterday 07:00 local
        result = _compute_next_run(job, last_run_at=last_run, now=now)
        assert result is not None
        base = datetime(2026, 4, 12, 5, 0, tzinfo=UTC)  # tomorrow 07:00 CEST
        assert base <= result < base + timedelta(seconds=_STAGGER_WINDOW)


# ---------------------------------------------------------------------------
# Schedule error auto-disable
# ---------------------------------------------------------------------------


class TestScheduleErrorAutoDisable:
    def test_auto_disable_after_three_errors(self, tmp_path, monkeypatch):
        from marcel_core.jobs.scheduler import JobScheduler
        from marcel_core.storage import _root

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        job = JobDefinition(
            name='bad-cron',
            users=['test'],
            trigger=TriggerSpec(type=TriggerType.CRON, cron='INVALID'),
            system_prompt='test',
            task='test',
            schedule_errors=2,  # already at 2
        )

        from marcel_core.jobs import save_job

        save_job(job)

        scheduler = JobScheduler()
        scheduler.schedule_job(job)

        # Job should have been auto-disabled
        from marcel_core.jobs import load_job

        reloaded = load_job(job.id)
        assert reloaded is not None
        assert reloaded.status == JobStatus.DISABLED
        assert reloaded.schedule_errors >= 3
        assert job.id not in scheduler._schedule

    def test_error_below_threshold_keeps_job_active(self, tmp_path, monkeypatch):
        """The first schedule failures only bump the counter — the job stays
        ACTIVE (though unscheduled) until the third strike."""
        from marcel_core.jobs import load_job, save_job
        from marcel_core.jobs.scheduler import JobScheduler
        from marcel_core.storage import _root

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        job = JobDefinition(
            name='bad-cron-soft',
            users=['test'],
            trigger=TriggerSpec(type=TriggerType.CRON, cron='INVALID'),
            system_prompt='test',
            task='test',
        )
        save_job(job)

        scheduler = JobScheduler()
        scheduler.schedule_job(job)

        reloaded = load_job(job.id)
        assert reloaded is not None
        assert reloaded.status == JobStatus.ACTIVE
        assert reloaded.schedule_errors == 1
        assert job.id not in scheduler._schedule


# ---------------------------------------------------------------------------
# Startup catchup
# ---------------------------------------------------------------------------


class TestStartupCatchup:
    def test_overdue_jobs_get_staggered(self, tmp_path, monkeypatch):
        """Jobs that were overdue according to saved state are staggered."""
        from marcel_core.jobs.scheduler import JobScheduler
        from marcel_core.storage import _root

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        # Create a job
        job = JobDefinition(
            name='catchup-test',
            users=['test'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            system_prompt='test',
            task='test',
        )
        from marcel_core.jobs import save_job

        save_job(job)

        # Write saved state with a past-due time
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        state = {job.id: past}
        state_path = tmp_path / 'scheduler_state.json'
        state_path.write_text(json.dumps(state))

        scheduler = JobScheduler()
        loaded = scheduler._load_state()
        assert job.id in loaded
