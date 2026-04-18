"""Tests for the habitat scheduled-jobs hook (ISSUE-82f52b).

Covers two layers:

- ``_validate_scheduled_jobs`` — strict validation of the
  ``scheduled_jobs:`` block in ``integration.yaml`` (raises
  :class:`HabitatRollback` on malformed input so the whole habitat is
  rolled back).
- ``_ensure_habitat_jobs`` — synthesizes :class:`JobDefinition` records
  from the validated specs at scheduler startup, with stable IDs and
  orphan reconciliation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marcel_core.skills.integrations import (
    _EXTERNAL_MODULE_PREFIX,
    HabitatRollback,
    IntegrationMetadata,
    ScheduledJobSpec,
    _discover_external,
    _metadata,
    _registry,
    _validate_scheduled_jobs,
)

VALID_HANDLER = (
    'from marcel_core.plugin import register\n'
    '\n'
    '@register("syncer.run")\n'
    'async def run(params, user_slug):\n'
    '    return "ran"\n'
)


@pytest.fixture
def isolated_registry(monkeypatch):
    """Snapshot/restore the global integration registry around each test."""
    saved_registry = dict(_registry)
    saved_metadata = dict(_metadata)
    yield
    _registry.clear()
    _registry.update(saved_registry)
    _metadata.clear()
    _metadata.update(saved_metadata)


@pytest.fixture
def cleanup_external_modules():
    """Drop dynamically-imported habitat modules at end of test."""
    import sys

    yield
    for name in list(sys.modules):
        if name.startswith(_EXTERNAL_MODULE_PREFIX):
            sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path, monkeypatch):
    """Point the jobs storage at a per-test data root."""
    from marcel_core.storage import _root

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


def _write_habitat(root: Path, name: str, body: str, *, yaml: str | None = None) -> Path:
    pkg = root / 'integrations' / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / '__init__.py').write_text(body, encoding='utf-8')
    if yaml is not None:
        (pkg / 'integration.yaml').write_text(yaml, encoding='utf-8')
    return pkg


# ---------------------------------------------------------------------------
# Validation layer
# ---------------------------------------------------------------------------


class TestValidateScheduledJobs:
    def test_none_and_missing_returns_empty(self):
        assert _validate_scheduled_jobs('foo', None, ['foo.bar']) == []

    def test_minimal_cron_entry_parses(self):
        result = _validate_scheduled_jobs(
            'foo',
            [{'name': 'job-a', 'handler': 'foo.bar', 'cron': '0 * * * *'}],
            ['foo.bar'],
        )
        assert len(result) == 1
        spec = result[0]
        assert spec.name == 'job-a'
        assert spec.handler == 'foo.bar'
        assert spec.cron == '0 * * * *'
        assert spec.interval_seconds is None
        assert spec.notify == 'silent'
        assert spec.params == {}

    def test_interval_entry_parses(self):
        result = _validate_scheduled_jobs(
            'foo',
            [{'name': 'job-a', 'handler': 'foo.bar', 'interval_seconds': 600}],
            ['foo.bar'],
        )
        assert result[0].interval_seconds == 600
        assert result[0].cron is None

    def test_full_entry_with_overrides(self):
        result = _validate_scheduled_jobs(
            'foo',
            [
                {
                    'name': 'job-a',
                    'handler': 'foo.bar',
                    'cron': '*/15 * * * *',
                    'params': {'x': '1'},
                    'description': 'desc',
                    'notify': 'on_failure',
                    'channel': 'telegram',
                    'timezone': 'Europe/Brussels',
                    'task': 'do the thing',
                    'system_prompt': 'you are X',
                    'model': 'anthropic:claude-sonnet-4-6',
                }
            ],
            ['foo.bar'],
        )
        spec = result[0]
        assert spec.params == {'x': '1'}
        assert spec.description == 'desc'
        assert spec.notify == 'on_failure'
        assert spec.timezone == 'Europe/Brussels'
        assert spec.task == 'do the thing'
        assert spec.system_prompt == 'you are X'
        assert spec.model == 'anthropic:claude-sonnet-4-6'

    def test_block_must_be_list(self):
        with pytest.raises(HabitatRollback, match='must be a list'):
            _validate_scheduled_jobs('foo', {'oops': 1}, ['foo.bar'])

    def test_entry_must_be_mapping(self):
        with pytest.raises(HabitatRollback, match='must be a mapping'):
            _validate_scheduled_jobs('foo', ['just-a-string'], ['foo.bar'])

    def test_missing_name_raises(self):
        with pytest.raises(HabitatRollback, match='name'):
            _validate_scheduled_jobs(
                'foo',
                [{'handler': 'foo.bar', 'cron': '0 * * * *'}],
                ['foo.bar'],
            )

    def test_handler_not_in_provides_raises(self):
        with pytest.raises(HabitatRollback, match='not listed in this habitat'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'other.x', 'cron': '0 * * * *'}],
                ['foo.bar'],
            )

    def test_missing_trigger_raises(self):
        with pytest.raises(HabitatRollback, match="exactly one of 'cron' or 'interval_seconds'"):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar'}],
                ['foo.bar'],
            )

    def test_both_triggers_raises(self):
        with pytest.raises(HabitatRollback, match='exactly one'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'cron': '0 * * * *', 'interval_seconds': 60}],
                ['foo.bar'],
            )

    def test_invalid_cron_raises(self):
        with pytest.raises(HabitatRollback, match='invalid cron'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'cron': 'not a cron'}],
                ['foo.bar'],
            )

    def test_negative_interval_raises(self):
        with pytest.raises(HabitatRollback, match='positive integer'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'interval_seconds': -5}],
                ['foo.bar'],
            )

    def test_bool_interval_rejected(self):
        # booleans are ints in Python — guard against True/False being accepted
        with pytest.raises(HabitatRollback, match='positive integer'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'interval_seconds': True}],
                ['foo.bar'],
            )

    def test_duplicate_name_within_habitat_raises(self):
        with pytest.raises(HabitatRollback, match='duplicate name'):
            _validate_scheduled_jobs(
                'foo',
                [
                    {'name': 'a', 'handler': 'foo.bar', 'cron': '0 * * * *'},
                    {'name': 'a', 'handler': 'foo.bar', 'cron': '0 1 * * *'},
                ],
                ['foo.bar'],
            )

    def test_duplicate_name_across_habitats_raises(self, isolated_registry):
        _metadata['other'] = IntegrationMetadata(
            name='other',
            scheduled_jobs=[ScheduledJobSpec(name='shared', handler='other.x', cron='0 * * * *')],
        )
        with pytest.raises(HabitatRollback, match='collides'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'shared', 'handler': 'foo.bar', 'cron': '0 * * * *'}],
                ['foo.bar'],
            )

    def test_invalid_notify_value_raises(self):
        with pytest.raises(HabitatRollback, match='notify'):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'cron': '0 * * * *', 'notify': 'maybe'}],
                ['foo.bar'],
            )

    def test_params_must_be_mapping(self):
        with pytest.raises(HabitatRollback, match="'params'"):
            _validate_scheduled_jobs(
                'foo',
                [{'name': 'a', 'handler': 'foo.bar', 'cron': '0 * * * *', 'params': [1, 2]}],
                ['foo.bar'],
            )


# ---------------------------------------------------------------------------
# Discovery integration — full habitat round-trip with rollback
# ---------------------------------------------------------------------------


class TestDiscoveryRollback:
    def test_valid_scheduled_jobs_publishes_metadata(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules
    ):
        from marcel_core.config import settings

        _write_habitat(
            tmp_path,
            'syncer',
            VALID_HANDLER,
            yaml=(
                'name: syncer\n'
                'provides: [syncer.run]\n'
                'scheduled_jobs:\n'
                '  - name: nightly\n'
                '    handler: syncer.run\n'
                '    cron: "0 3 * * *"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        assert 'syncer.run' in _registry
        meta = _metadata.get('syncer')
        assert meta is not None
        assert len(meta.scheduled_jobs) == 1
        assert meta.scheduled_jobs[0].name == 'nightly'

    def test_malformed_scheduled_jobs_rolls_back_handlers(
        self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules, caplog
    ):
        """Bad scheduled_jobs entry must wipe both handlers and metadata."""
        from marcel_core.config import settings

        _write_habitat(
            tmp_path,
            'syncer',
            VALID_HANDLER,
            yaml=(
                'name: syncer\n'
                'provides: [syncer.run]\n'
                'scheduled_jobs:\n'
                '  - name: bad\n'
                '    handler: not.declared\n'
                '    cron: "0 3 * * *"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        with caplog.at_level('ERROR', logger='marcel_core.skills.integrations'):
            _discover_external()

        assert 'syncer.run' not in _registry
        assert 'syncer' not in _metadata
        assert any('rolled back' in r.message for r in caplog.records)

    def test_invalid_cron_rolls_back_handlers(self, tmp_path, monkeypatch, isolated_registry, cleanup_external_modules):
        from marcel_core.config import settings

        _write_habitat(
            tmp_path,
            'syncer',
            VALID_HANDLER,
            yaml=(
                'name: syncer\n'
                'provides: [syncer.run]\n'
                'scheduled_jobs:\n'
                '  - name: a\n'
                '    handler: syncer.run\n'
                '    cron: "not a cron"\n'
            ),
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(tmp_path))

        _discover_external()

        assert 'syncer.run' not in _registry
        assert 'syncer' not in _metadata


# ---------------------------------------------------------------------------
# Scheduler-side reconciliation
# ---------------------------------------------------------------------------


class TestEnsureHabitatJobs:
    def test_creates_job_definition_from_spec(self, isolated_registry):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.models import NotifyPolicy, TriggerType
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs, _habitat_job_id

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[
                ScheduledJobSpec(
                    name='nightly',
                    handler='syncer.run',
                    cron='0 3 * * *',
                    params={'x': '1'},
                )
            ],
        )

        _ensure_habitat_jobs()

        jobs = list_all_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == _habitat_job_id('syncer', 'nightly')
        assert job.template == 'habitat:syncer'
        assert job.users == []
        assert job.skills == ['syncer.run']
        assert job.trigger.type == TriggerType.CRON
        assert job.trigger.cron == '0 3 * * *'
        assert job.notify == NotifyPolicy.SILENT

    def test_default_task_includes_handler_and_params(self, isolated_registry):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[
                ScheduledJobSpec(
                    name='nightly',
                    handler='syncer.run',
                    cron='0 3 * * *',
                    params={'limit': '50'},
                )
            ],
        )

        _ensure_habitat_jobs()

        job = list_all_jobs()[0]
        assert 'syncer.run' in job.task
        assert 'limit' in job.task and '50' in job.task

    def test_overrides_replace_defaults(self, isolated_registry):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[
                ScheduledJobSpec(
                    name='nightly',
                    handler='syncer.run',
                    cron='0 3 * * *',
                    task='custom task body',
                    system_prompt='custom system prompt',
                    model='anthropic:claude-sonnet-4-6',
                )
            ],
        )

        _ensure_habitat_jobs()

        job = list_all_jobs()[0]
        assert job.task == 'custom task body'
        assert job.system_prompt == 'custom system prompt'
        assert job.model == 'anthropic:claude-sonnet-4-6'

    def test_idempotent_does_not_duplicate(self, isolated_registry):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[ScheduledJobSpec(name='nightly', handler='syncer.run', cron='0 3 * * *')],
        )

        _ensure_habitat_jobs()
        _ensure_habitat_jobs()
        _ensure_habitat_jobs()

        assert len(list_all_jobs()) == 1

    def test_orphan_jobs_deleted_when_habitat_unloads(self, isolated_registry):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[ScheduledJobSpec(name='nightly', handler='syncer.run', cron='0 3 * * *')],
        )
        _ensure_habitat_jobs()
        assert len(list_all_jobs()) == 1

        # Habitat removed (e.g. user uninstalled it) → next reconciliation
        # must drop the job from disk.
        _metadata.clear()
        _ensure_habitat_jobs()

        assert list_all_jobs() == []

    def test_orphan_within_habitat_reconciled(self, isolated_registry):
        """Renaming an entry leaves no stale job behind."""
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs, _habitat_job_id

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[ScheduledJobSpec(name='old-name', handler='syncer.run', cron='0 3 * * *')],
        )
        _ensure_habitat_jobs()
        old_id = _habitat_job_id('syncer', 'old-name')
        assert any(j.id == old_id for j in list_all_jobs())

        # User renamed the entry in integration.yaml
        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[ScheduledJobSpec(name='new-name', handler='syncer.run', cron='0 3 * * *')],
        )
        _ensure_habitat_jobs()

        jobs = list_all_jobs()
        ids = {j.id for j in jobs}
        assert old_id not in ids
        assert _habitat_job_id('syncer', 'new-name') in ids

    def test_does_not_delete_non_habitat_jobs(self, isolated_registry):
        """Reconciliation must only touch habitat-templated jobs."""
        from marcel_core.jobs import list_all_jobs, save_job
        from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        unrelated = JobDefinition(
            name='unrelated',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            system_prompt='do',
            task='go',
            template='sync',
        )
        save_job(unrelated)

        _ensure_habitat_jobs()  # no habitats loaded

        jobs = list_all_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == unrelated.id


class TestStableJobIDs:
    def test_same_inputs_same_id(self):
        from marcel_core.jobs.scheduler import _habitat_job_id

        assert _habitat_job_id('foo', 'bar') == _habitat_job_id('foo', 'bar')

    def test_different_habitats_different_ids(self):
        from marcel_core.jobs.scheduler import _habitat_job_id

        assert _habitat_job_id('a', 'job') != _habitat_job_id('b', 'job')

    def test_different_entry_names_different_ids(self):
        from marcel_core.jobs.scheduler import _habitat_job_id

        assert _habitat_job_id('foo', 'one') != _habitat_job_id('foo', 'two')


# ---------------------------------------------------------------------------
# End-to-end: scheduler dispatches a habitat job through the existing
# pipeline. Mocks the executor to confirm the JobDefinition we created
# would actually flow through ``_dispatch``.
# ---------------------------------------------------------------------------


class TestDispatchesHabitatJob:
    @pytest.mark.asyncio
    async def test_dispatch_invokes_executor_with_habitat_job(self, isolated_registry):
        from unittest.mock import AsyncMock, patch

        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.models import JobRun, RunStatus
        from marcel_core.jobs.scheduler import JobScheduler, _ensure_habitat_jobs

        _metadata['syncer'] = IntegrationMetadata(
            name='syncer',
            provides=['syncer.run'],
            scheduled_jobs=[
                ScheduledJobSpec(
                    name='nightly',
                    handler='syncer.run',
                    cron='0 3 * * *',
                    params={'k': 'v'},
                )
            ],
        )
        _ensure_habitat_jobs()
        job = list_all_jobs()[0]

        scheduler = JobScheduler()
        captured: dict[str, object] = {}

        async def fake_exec(job_def, trigger_reason, user_slug):
            captured['name'] = job_def.name
            captured['template'] = job_def.template
            captured['user_slug'] = user_slug
            return JobRun(job_id=job_def.id, status=RunStatus.COMPLETED)

        with (
            patch(
                'marcel_core.jobs.executor.execute_job_with_retries',
                new=AsyncMock(side_effect=fake_exec),
            ),
            patch.object(scheduler, '_save_state'),
        ):
            await scheduler._dispatch(job.id)

        assert captured['name'] == 'nightly'
        assert captured['template'] == 'habitat:syncer'
        # System-scope jobs run under the reserved _system slug
        assert captured['user_slug'] == '_system'
