"""Tests for run retention and cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from marcel_core.jobs.models import JobDefinition, JobRun, RunStatus, TriggerSpec, TriggerType
from marcel_core.storage import _root


class TestCleanupOldRuns:
    def _make_job(self, tmp_path, monkeypatch) -> JobDefinition:
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        job = JobDefinition(
            name='test',
            users=['test'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            system_prompt='test',
            task='test',
            retention_days=7,
        )
        from marcel_core.jobs import save_job

        save_job(job)
        return job

    def test_removes_old_runs(self, tmp_path, monkeypatch):
        from marcel_core.jobs import append_run, cleanup_old_runs, read_runs

        job = self._make_job(tmp_path, monkeypatch)

        # Add old run (8 days ago)
        old_run = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(days=8),
            finished_at=datetime.now(UTC) - timedelta(days=8),
            output='old',
        )
        append_run(job.id, 'test', old_run)

        # Add recent run (1 day ago)
        new_run = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(days=1),
            finished_at=datetime.now(UTC) - timedelta(days=1),
            output='new',
        )
        append_run(job.id, 'test', new_run)

        removed = cleanup_old_runs(job.id, 7)
        assert removed == 1

        remaining = read_runs(job.id, 'test')
        assert len(remaining) == 1
        assert remaining[0].output == 'new'

    def test_keeps_runs_without_finished_at(self, tmp_path, monkeypatch):
        from marcel_core.jobs import append_run, cleanup_old_runs, read_runs

        job = self._make_job(tmp_path, monkeypatch)

        # Run with no finished_at (still running or stuck)
        running = JobRun(
            job_id=job.id,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(days=30),
        )
        append_run(job.id, 'test', running)

        removed = cleanup_old_runs(job.id, 7)
        assert removed == 0

        remaining = read_runs(job.id, 'test')
        assert len(remaining) == 1

    def test_no_runs_file(self, tmp_path, monkeypatch):
        from marcel_core.jobs import cleanup_old_runs

        job = self._make_job(tmp_path, monkeypatch)
        removed = cleanup_old_runs(job.id, 7)
        assert removed == 0

    def test_all_recent(self, tmp_path, monkeypatch):
        from marcel_core.jobs import append_run, cleanup_old_runs

        job = self._make_job(tmp_path, monkeypatch)

        for i in range(3):
            run = JobRun(
                job_id=job.id,
                status=RunStatus.COMPLETED,
                started_at=datetime.now(UTC) - timedelta(hours=i),
                finished_at=datetime.now(UTC) - timedelta(hours=i),
            )
            append_run(job.id, 'test', run)

        removed = cleanup_old_runs(job.id, 7)
        assert removed == 0
