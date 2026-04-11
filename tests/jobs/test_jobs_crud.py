"""Scenario-based tests for the jobs CRUD layer (jobs/__init__.py).

Exercises save, load, list, delete, run log append/read, and cleanup
through realistic multi-job, multi-user workflows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marcel_core.jobs.models import (
    JobDefinition,
    JobRun,
    RunStatus,
    TriggerSpec,
    TriggerType,
)
from marcel_core.storage import _root


def _make_job(user: str = 'alice', name: str = 'test-job', **kw) -> JobDefinition:
    return JobDefinition(
        name=name,
        user_slug=user,
        trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
        system_prompt='do stuff',
        task='run stuff',
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_round_trip(self):
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        save_job(job)
        loaded = load_job('alice', job.id)
        assert loaded is not None
        assert loaded.id == job.id
        assert loaded.name == 'test-job'
        assert loaded.user_slug == 'alice'

    def test_load_nonexistent_returns_none(self):
        from marcel_core.jobs import load_job

        assert load_job('alice', 'no-such-job') is None

    def test_save_overwrites(self):
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        save_job(job)
        job.name = 'renamed'
        save_job(job)
        loaded = load_job('alice', job.id)
        assert loaded is not None
        assert loaded.name == 'renamed'


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_list_empty_user(self):
        from marcel_core.jobs import list_jobs

        assert list_jobs('nobody') == []

    def test_list_multiple_jobs(self):
        from marcel_core.jobs import list_jobs, save_job

        for i in range(3):
            save_job(_make_job(name=f'job-{i}'))
        jobs = list_jobs('alice')
        assert len(jobs) == 3

    def test_list_all_jobs_across_users(self):
        from marcel_core.jobs import list_all_jobs, save_job

        save_job(_make_job(user='alice', name='a-job'))
        save_job(_make_job(user='bob', name='b-job'))
        all_jobs = list_all_jobs()
        assert len(all_jobs) == 2
        names = {j.name for j in all_jobs}
        assert names == {'a-job', 'b-job'}

    def test_list_all_jobs_no_users_dir(self, tmp_path, monkeypatch):
        """If the users directory doesn't exist, list_all_jobs returns empty."""
        from marcel_core.jobs import list_all_jobs

        # Point to an empty dir with no users/ subdir
        empty = tmp_path / 'empty'
        empty.mkdir()
        monkeypatch.setattr(_root, '_DATA_ROOT', empty)
        assert list_all_jobs() == []


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteJob:
    def test_delete_existing(self):
        from marcel_core.jobs import delete_job, load_job, save_job

        job = _make_job()
        save_job(job)
        assert delete_job('alice', job.id) is True
        assert load_job('alice', job.id) is None

    def test_delete_nonexistent(self):
        from marcel_core.jobs import delete_job

        assert delete_job('alice', 'nope') is False


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


class TestRunLog:
    def test_append_and_read(self):
        from marcel_core.jobs import append_run, read_runs, save_job

        job = _make_job()
        save_job(job)

        for i in range(5):
            run = JobRun(
                job_id=job.id,
                status=RunStatus.COMPLETED,
                started_at=datetime.now(UTC) - timedelta(hours=5 - i),
                finished_at=datetime.now(UTC) - timedelta(hours=5 - i),
                output=f'run-{i}',
            )
            append_run('alice', job.id, run)

        runs = read_runs('alice', job.id)
        assert len(runs) == 5
        # Newest first
        assert runs[0].output == 'run-4'

    def test_read_with_limit(self):
        from marcel_core.jobs import append_run, read_runs, save_job

        job = _make_job()
        save_job(job)
        for i in range(10):
            append_run(
                'alice',
                job.id,
                JobRun(
                    job_id=job.id,
                    status=RunStatus.COMPLETED,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                ),
            )

        runs = read_runs('alice', job.id, limit=3)
        assert len(runs) == 3

    def test_read_empty(self):
        from marcel_core.jobs import read_runs, save_job

        job = _make_job()
        save_job(job)
        assert read_runs('alice', job.id) == []

    def test_last_run(self):
        from marcel_core.jobs import append_run, last_run, save_job

        job = _make_job()
        save_job(job)
        assert last_run('alice', job.id) is None

        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='latest')
        append_run('alice', job.id, run)
        lr = last_run('alice', job.id)
        assert lr is not None
        assert lr.output == 'latest'

    def test_malformed_run_line_skipped(self, tmp_path):
        from marcel_core.jobs import read_runs, save_job

        job = _make_job()
        save_job(job)
        runs_path = tmp_path / 'users' / 'alice' / 'jobs' / job.id / 'runs.jsonl'
        runs_path.write_text('not valid json\n{"run_id":"x","job_id":"y"}\n')

        runs = read_runs('alice', job.id)
        # Malformed line skipped, valid one parsed
        assert len(runs) == 1

    def test_load_corrupt_job_returns_none(self, tmp_path):
        """A corrupt job.json returns None from load_job."""
        from marcel_core.jobs import load_job

        job_dir = tmp_path / 'users' / 'alice' / 'jobs' / 'corrupt'
        job_dir.mkdir(parents=True)
        (job_dir / 'job.json').write_text('{bad json}')
        assert load_job('alice', 'corrupt') is None
