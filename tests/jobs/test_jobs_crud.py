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

    def test_migration_skips_stray_files_and_unreadable_jobs(self, tmp_path):
        """Stray files, dirs without job.json, corrupt JSON, and invalid schemas
        are all skipped without aborting the rest of the migration."""
        from marcel_core.jobs import list_all_jobs, migrate_legacy_jobs

        users = tmp_path / 'users'
        users.mkdir(parents=True, exist_ok=True)
        (users / 'README.txt').write_text('not a user dir', encoding='utf-8')

        jobs_dir = users / 'alice' / 'jobs'
        jobs_dir.mkdir(parents=True)
        (jobs_dir / 'notes.txt').write_text('not a job dir', encoding='utf-8')
        (jobs_dir / 'empty-dir').mkdir()
        bad_json = jobs_dir / 'bad-json'
        bad_json.mkdir()
        (bad_json / 'job.json').write_text('{nope', encoding='utf-8')
        bad_schema = jobs_dir / 'bad-schema'
        bad_schema.mkdir()
        (bad_schema / 'job.json').write_text(json.dumps({'name': 'no trigger'}), encoding='utf-8')

        self._write_legacy(tmp_path, 'alice', 'good1')

        # Only the well-formed legacy job migrates; the rest are skipped.
        assert migrate_legacy_jobs() == 1
        jobs = list_all_jobs()
        assert [j.id for j in jobs] == ['good1']
        # The whole legacy tree is removed regardless of the skipped entries.
        assert not (tmp_path / 'users' / 'alice' / 'jobs').exists()

    def test_migration_drops_user_slug_when_users_already_present(self, tmp_path):
        """A legacy record carrying both ``users`` and ``user_slug`` keeps the
        explicit ``users`` list and silently drops the legacy key."""
        from marcel_core.jobs import load_job, migrate_legacy_jobs

        self._write_legacy(tmp_path, 'alice', 'dual', users=['alice', 'bob'])

        assert migrate_legacy_jobs() == 1
        job = load_job('dual')
        assert job is not None
        assert job.users == ['alice', 'bob']

    def test_migration_tolerates_missing_target_dir_for_runs(self, tmp_path, monkeypatch):
        """If the migrated job's directory cannot be located after save (e.g.
        removed between save and lookup), the runs move is skipped instead of
        crashing the migration."""
        import marcel_core.jobs as jobs_module

        legacy_dir = self._write_legacy(tmp_path, 'alice', 'runsy')
        run = JobRun(job_id='runsy', status=RunStatus.COMPLETED, output='old-run')
        (legacy_dir / 'runs.jsonl').write_text(run.model_dump_json() + '\n', encoding='utf-8')

        monkeypatch.setattr(jobs_module, '_find_job_dir_by_id', lambda job_id: None)

        assert jobs_module.migrate_legacy_jobs() == 1
        # No runs file was created anywhere — the move was skipped.
        assert not list((tmp_path / 'jobs').rglob('*.jsonl'))


# ---------------------------------------------------------------------------
# Parsing helpers and defensive paths
# ---------------------------------------------------------------------------


class TestParsingHelpers:
    def test_read_frontmatter_only_unreadable_path(self, tmp_path):
        from marcel_core.jobs import _read_frontmatter_only

        assert _read_frontmatter_only(tmp_path / 'does-not-exist.md') is None

    def test_parse_frontmatter_without_marker(self):
        from marcel_core.jobs import _parse_frontmatter

        fm, body = _parse_frontmatter('plain text, no frontmatter')
        assert fm == {}
        assert body == 'plain text, no frontmatter'

    def test_parse_frontmatter_unterminated(self):
        from marcel_core.jobs import _parse_frontmatter

        text = '---\nid: x\nnever closed'
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_parse_frontmatter_invalid_yaml(self):
        from marcel_core.jobs import _parse_frontmatter

        fm, body = _parse_frontmatter('---\n[unclosed\n---\nbody text')
        assert fm == {}
        assert body == 'body text'

    def test_parse_body_task_before_system_prompt(self):
        """Section order is not fixed — ## Task may come first in a hand-edited JOB.md."""
        from marcel_core.jobs import _parse_body

        body = '## Task\n\ndo the thing\n\n## System Prompt\n\nyou are a robot\n'
        system_prompt, task = _parse_body(body)
        assert system_prompt == 'you are a robot'
        assert task == 'do the thing'


class TestDefensivePaths:
    def test_helpers_tolerate_missing_jobs_root(self, tmp_path, monkeypatch):
        """If the jobs root vanishes between creation and scan, lookups degrade
        to empty results instead of crashing."""
        import marcel_core.jobs as jobs_module

        monkeypatch.setattr(jobs_module, '_jobs_root', lambda: tmp_path / 'never-created')
        assert jobs_module._find_job_dir_by_id('whatever') is None
        assert jobs_module.list_all_jobs() == []

    def test_stray_entries_in_jobs_root_are_skipped(self):
        """Files, underscore-prefixed dirs, and dirs without a JOB.md never
        surface as jobs — neither in list_all_jobs nor in id lookups."""
        from marcel_core.jobs import _jobs_root, list_all_jobs, load_job, save_job

        root = _jobs_root()
        (root / 'no-job-md').mkdir()
        (root / '_private').mkdir()
        (root / 'stray.txt').write_text('x', encoding='utf-8')

        job = _make_job()
        save_job(job)

        assert [j.id for j in list_all_jobs()] == [job.id]
        assert load_job('no-such-id') is None


# ---------------------------------------------------------------------------
# state.json edge cases
# ---------------------------------------------------------------------------


class TestStateJson:
    def test_missing_state_json_loads_defaults(self):
        from marcel_core.jobs import load_job, save_job

        job = _make_job(consecutive_errors=4)
        d = save_job(job)
        (d / 'state.json').unlink()

        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.consecutive_errors == 0  # state slice fell back to defaults

    def test_non_dict_state_json_ignored(self):
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        d = save_job(job)
        (d / 'state.json').write_text('[1, 2, 3]', encoding='utf-8')

        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.consecutive_errors == 0

    def test_corrupt_state_json_warns_and_loads(self, caplog):
        from marcel_core.jobs import load_job, save_job

        job = _make_job()
        d = save_job(job)
        (d / 'state.json').write_text('not json at all', encoding='utf-8')

        with caplog.at_level('WARNING', logger='marcel_core.jobs'):
            loaded = load_job(job.id)
        assert loaded is not None
        assert 'Failed to read state.json' in caplog.text


# ---------------------------------------------------------------------------
# Run log / cleanup edge cases
# ---------------------------------------------------------------------------


class TestRunLogEdgeCases:
    def test_blank_lines_in_run_log_skipped(self):
        from marcel_core.jobs import _find_job_dir_by_id, append_run, read_runs, save_job

        job = _make_job()
        save_job(job)
        append_run(job.id, 'alice', JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='real'))

        d = _find_job_dir_by_id(job.id)
        assert d is not None
        with (d / 'runs' / 'alice.jsonl').open('a', encoding='utf-8') as f:
            f.write('\n\n')

        runs = read_runs(job.id, 'alice')
        assert len(runs) == 1
        assert runs[0].output == 'real'

    def test_cleanup_nonexistent_job_returns_zero(self):
        from marcel_core.jobs import cleanup_old_runs

        assert cleanup_old_runs('ghost-job', 7) == 0

    def test_cleanup_keeps_blank_and_malformed_lines_out_of_removed_count(self):
        """Blank lines are dropped silently; malformed lines are kept (never
        deleted as 'old') so no data is lost to a parse bug."""
        from marcel_core.jobs import _find_job_dir_by_id, cleanup_old_runs, save_job

        job = _make_job()
        save_job(job)
        d = _find_job_dir_by_id(job.id)
        assert d is not None
        (d / 'runs').mkdir(exist_ok=True)

        old = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(days=30),
            finished_at=datetime.now(UTC) - timedelta(days=30),
            output='ancient',
        )
        recent = JobRun(
            job_id=job.id,
            status=RunStatus.COMPLETED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            output='fresh',
        )
        lines = [old.model_dump_json(), '', 'this is not json', recent.model_dump_json()]
        (d / 'runs' / 'alice.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        removed = cleanup_old_runs(job.id, 7)
        assert removed == 1

        remaining = (d / 'runs' / 'alice.jsonl').read_text(encoding='utf-8')
        assert 'this is not json' in remaining  # malformed kept, not lost
        assert 'fresh' in remaining
        assert 'ancient' not in remaining
