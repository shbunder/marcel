"""Scenario-based tests for the jobs CRUD layer (jobs/__init__.py).

Exercises save, load, list, delete, run log append/read, cleanup, and
migration through realistic multi-job, multi-user workflows against the
flat ``<data_root>/jobs/<slug>/`` layout.
"""

from __future__ import annotations

import json
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


def _make_job(users: list[str] | None = None, name: str = 'test-job', **kw) -> JobDefinition:
    kw.setdefault('system_prompt', 'do stuff')
    kw.setdefault('task', 'run stuff')
    return JobDefinition(
        name=name,
        users=['alice'] if users is None else users,
        trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
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
        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.id == job.id
        assert loaded.name == 'test-job'
        assert loaded.users == ['alice']

    def test_load_nonexistent_returns_none(self):
        from marcel_core.jobs import load_job

        assert load_job('no-such-job') is None

    def test_save_overwrites(self):
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        save_job(job)
        # Renaming keeps the same directory (directory-by-id).
        job.name = 'renamed'
        save_job(job)
        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.name == 'renamed'

    def test_save_writes_job_md_and_state_json(self, tmp_path):
        from marcel_core.jobs import save_job

        job = _make_job()
        d = save_job(job)
        assert (d / 'JOB.md').exists()
        assert (d / 'state.json').exists()

    def test_job_md_has_frontmatter_and_sections(self, tmp_path):
        from marcel_core.jobs import save_job

        job = _make_job(name='News sync', system_prompt='Scrape RSS.', task='Fetch feeds.')
        d = save_job(job)
        text = (d / 'JOB.md').read_text(encoding='utf-8')
        assert text.startswith('---\n')
        assert 'name: News sync' in text
        assert 'users:' in text
        assert '## System Prompt' in text
        assert '## Task' in text
        assert 'Scrape RSS.' in text
        assert 'Fetch feeds.' in text

    def test_state_json_contains_mutable_fields_only(self, tmp_path):
        from marcel_core.jobs import save_job

        job = _make_job(consecutive_errors=3, schedule_errors=1)
        d = save_job(job)
        state = json.loads((d / 'state.json').read_text(encoding='utf-8'))
        assert state['consecutive_errors'] == 3
        assert state['schedule_errors'] == 1
        assert 'name' not in state
        assert 'system_prompt' not in state

    def test_slug_deduplicates_on_name_collision(self):
        from marcel_core.jobs import _jobs_root, save_job

        job_a = _make_job(name='Digest')
        job_b = _make_job(name='Digest', users=['bob'])
        save_job(job_a)
        save_job(job_b)
        slugs = sorted(d.name for d in _jobs_root().iterdir() if d.is_dir())
        assert slugs == ['digest', 'digest-2']

    def test_load_migrates_legacy_unqualified_model(self, tmp_path):
        """An unqualified model name on disk self-heals to ``anthropic:*``."""
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        d = save_job(job)
        # Mutate the file to strip the provider prefix
        text = (d / 'JOB.md').read_text(encoding='utf-8')
        text = text.replace('anthropic:claude-haiku-4-5-20251001', 'claude-haiku-4-5-20251001')
        (d / 'JOB.md').write_text(text, encoding='utf-8')

        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.model == 'anthropic:claude-haiku-4-5-20251001'

        # File was rewritten — next load is idempotent.
        rewritten = (d / 'JOB.md').read_text(encoding='utf-8')
        assert 'anthropic:claude-haiku-4-5-20251001' in rewritten

    def test_load_leaves_qualified_model_unchanged(self):
        """Already-qualified strings (incl. ``local:*``) pass through untouched."""
        from marcel_core.jobs import load_job, save_job

        job = _make_job(model='local:qwen3.5:4b')
        save_job(job)
        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.model == 'local:qwen3.5:4b'


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

        save_job(_make_job(users=['alice'], name='a-job'))
        save_job(_make_job(users=['bob'], name='b-job'))
        all_jobs = list_all_jobs()
        assert len(all_jobs) == 2
        names = {j.name for j in all_jobs}
        assert names == {'a-job', 'b-job'}

    def test_list_jobs_filters_by_membership(self):
        from marcel_core.jobs import list_jobs, save_job

        save_job(_make_job(users=['alice'], name='alice-only'))
        save_job(_make_job(users=['bob'], name='bob-only'))
        save_job(_make_job(users=['alice', 'bob'], name='shared'))

        alice_jobs = {j.name for j in list_jobs('alice')}
        bob_jobs = {j.name for j in list_jobs('bob')}
        assert alice_jobs == {'alice-only', 'shared'}
        assert bob_jobs == {'bob-only', 'shared'}

    def test_list_system_jobs_excluded_from_user_list(self):
        from marcel_core.jobs import list_jobs, list_system_jobs, save_job

        save_job(_make_job(users=[], name='news-sync'))
        assert list_jobs('alice') == []
        system = list_system_jobs()
        assert len(system) == 1
        assert system[0].name == 'news-sync'

    def test_list_all_jobs_no_jobs_dir(self, tmp_path, monkeypatch):
        """If the jobs directory doesn't exist, list_all_jobs returns empty."""
        from marcel_core.jobs import list_all_jobs

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
        assert delete_job(job.id) is True
        assert load_job(job.id) is None

    def test_delete_nonexistent(self):
        from marcel_core.jobs import delete_job

        assert delete_job('nope') is False


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
            append_run(job.id, 'alice', run)

        runs = read_runs(job.id, 'alice')
        assert len(runs) == 5
        # Newest first
        assert runs[0].output == 'run-4'

    def test_per_user_runs_isolated(self):
        from marcel_core.jobs import append_run, read_runs, save_job

        job = _make_job(users=['alice', 'bob'])
        save_job(job)

        append_run(job.id, 'alice', JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='alice-1'))
        append_run(job.id, 'bob', JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='bob-1'))

        alice_runs = read_runs(job.id, 'alice')
        bob_runs = read_runs(job.id, 'bob')
        assert len(alice_runs) == 1 and alice_runs[0].output == 'alice-1'
        assert len(bob_runs) == 1 and bob_runs[0].output == 'bob-1'

    def test_system_scope_uses_system_runs_file(self):
        from marcel_core.jobs import SYSTEM_USER, _find_job_dir_by_id, append_run, save_job

        job = _make_job(users=[])
        save_job(job)
        append_run(job.id, None, JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='sys'))

        d = _find_job_dir_by_id(job.id)
        assert d is not None
        assert (d / 'runs' / f'{SYSTEM_USER}.jsonl').exists()

    def test_read_with_limit(self):
        from marcel_core.jobs import append_run, read_runs, save_job

        job = _make_job()
        save_job(job)
        for i in range(10):
            append_run(
                job.id,
                'alice',
                JobRun(
                    job_id=job.id,
                    status=RunStatus.COMPLETED,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                ),
            )

        runs = read_runs(job.id, 'alice', limit=3)
        assert len(runs) == 3

    def test_read_empty(self):
        from marcel_core.jobs import read_runs, save_job

        job = _make_job()
        save_job(job)
        assert read_runs(job.id, 'alice') == []

    def test_last_run(self):
        from marcel_core.jobs import append_run, last_run, save_job

        job = _make_job()
        save_job(job)
        assert last_run(job.id, 'alice') is None

        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='latest')
        append_run(job.id, 'alice', run)
        lr = last_run(job.id, 'alice')
        assert lr is not None
        assert lr.output == 'latest'

    def test_malformed_run_line_skipped(self, tmp_path):
        from marcel_core.jobs import _find_job_dir_by_id, read_runs, save_job

        job = _make_job()
        save_job(job)
        d = _find_job_dir_by_id(job.id)
        assert d is not None
        (d / 'runs').mkdir(parents=True, exist_ok=True)
        (d / 'runs' / 'alice.jsonl').write_text('not valid json\n{"run_id":"x","job_id":"y"}\n')

        runs = read_runs(job.id, 'alice')
        # Malformed line skipped, valid one parsed
        assert len(runs) == 1

    def test_load_corrupt_job_md_returns_none(self, tmp_path):
        """A corrupt JOB.md returns None from load_job."""
        from marcel_core.jobs import _jobs_root, load_job

        job_dir = _jobs_root() / 'corrupt'
        job_dir.mkdir(parents=True)
        (job_dir / 'JOB.md').write_text('---\nid: corrupt\n---\n\nno sections here\n')
        assert load_job('corrupt') is None


# ---------------------------------------------------------------------------
# Migration from legacy layout
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def _write_legacy(self, tmp_path, user: str, job_id: str, **overrides):
        legacy_dir = tmp_path / 'users' / user / 'jobs' / job_id
        legacy_dir.mkdir(parents=True)
        data = {
            'id': job_id,
            'name': overrides.pop('name', f'legacy-{job_id}'),
            'user_slug': user,
            'trigger': {'type': 'interval', 'interval_seconds': 3600},
            'system_prompt': 'do stuff',
            'task': 'run stuff',
            'model': 'anthropic:claude-haiku-4-5-20251001',
        }
        data.update(overrides)
        (legacy_dir / 'job.json').write_text(json.dumps(data), encoding='utf-8')
        return legacy_dir

    def test_migrates_single_legacy_job(self, tmp_path):
        from marcel_core.jobs import load_job, migrate_legacy_jobs

        self._write_legacy(tmp_path, 'alice', 'legacy1')

        migrated = migrate_legacy_jobs()
        assert migrated == 1

        job = load_job('legacy1')
        assert job is not None
        assert job.users == ['alice']
        assert job.name == 'legacy-legacy1'

        # Legacy directory was removed
        assert not (tmp_path / 'users' / 'alice' / 'jobs').exists()

    def test_migrates_runs_jsonl_to_per_user_path(self, tmp_path):
        from marcel_core.jobs import _find_job_dir_by_id, migrate_legacy_jobs, read_runs

        legacy_dir = self._write_legacy(tmp_path, 'alice', 'legacy2')
        run = JobRun(job_id='legacy2', status=RunStatus.COMPLETED, output='old-run')
        (legacy_dir / 'runs.jsonl').write_text(run.model_dump_json() + '\n', encoding='utf-8')

        migrate_legacy_jobs()

        new_dir = _find_job_dir_by_id('legacy2')
        assert new_dir is not None
        assert (new_dir / 'runs' / 'alice.jsonl').exists()
        runs = read_runs('legacy2', 'alice')
        assert len(runs) == 1
        assert runs[0].output == 'old-run'

    def test_legacy_unqualified_model_heals_during_migration(self, tmp_path):
        from marcel_core.jobs import load_job, migrate_legacy_jobs

        self._write_legacy(tmp_path, 'alice', 'legacy3', model='claude-haiku-4-5-20251001')
        migrate_legacy_jobs()
        job = load_job('legacy3')
        assert job is not None
        assert job.model == 'anthropic:claude-haiku-4-5-20251001'

    def test_migration_idempotent_when_nothing_to_migrate(self, tmp_path):
        from marcel_core.jobs import migrate_legacy_jobs

        assert migrate_legacy_jobs() == 0

    def test_migration_handles_multiple_users(self, tmp_path):
        from marcel_core.jobs import list_all_jobs, migrate_legacy_jobs

        self._write_legacy(tmp_path, 'alice', 'a1')
        self._write_legacy(tmp_path, 'bob', 'b1')

        assert migrate_legacy_jobs() == 2
        jobs = list_all_jobs()
        owners = {tuple(j.users) for j in jobs}
        assert owners == {('alice',), ('bob',)}
