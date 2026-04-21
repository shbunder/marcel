"""Scenario-based tests for jobs/tool.py — conversational job management tools.

Tests exercise create, list, get, update, delete, run_now, templates,
and cache read/write through realistic user workflows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.jobs.models import JobStatus, TriggerType
from marcel_core.storage import _root


def _ctx(user: str = 'alice') -> MagicMock:
    deps = MarcelDeps(user_slug=user, conversation_id='conv-1', channel='telegram')
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# Full lifecycle: create → list → get → update → delete
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_list(self):
        from marcel_core.jobs.tool import create_job, list_jobs

        # Patch scheduler to avoid side effects
        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            result = await create_job(
                _ctx(),
                name='Morning digest',
                task='Compile the morning digest',
                trigger_type='cron',
                system_prompt='You compose digests.',
                cron='0 7 * * *',
                notify='always',
            )
        assert 'Morning digest' in result
        assert 'Job created' in result

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            listing = await list_jobs(_ctx())
        assert 'Morning digest' in listing

    @pytest.mark.asyncio
    async def test_get_job_details(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.models import JobDefinition, TriggerSpec
        from marcel_core.jobs.tool import get_job

        job = JobDefinition(
            name='Bank sync',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            system_prompt='sync banks',
            task='Run banking.sync',
            consecutive_errors=2,
        )
        save_job(job)

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            result = await get_job(_ctx(), job.id)
        assert 'Bank sync' in result
        assert 'Consecutive errors: 2' in result

    @pytest.mark.asyncio
    async def test_get_job_not_found(self):
        from marcel_core.jobs.tool import get_job

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await get_job(_ctx(), 'nonexistent')
        assert 'not found' in result

    @pytest.mark.asyncio
    async def test_get_job_with_runs(self):
        from marcel_core.jobs import append_run, save_job
        from marcel_core.jobs.models import JobDefinition, JobRun, RunStatus, TriggerSpec
        from marcel_core.jobs.tool import get_job

        job = JobDefinition(
            name='test',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='t',
            task='t',
        )
        save_job(job)
        append_run(
            job.id,
            'alice',
            JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='hello world'),
        )
        append_run(
            job.id,
            'alice',
            JobRun(job_id=job.id, status=RunStatus.FAILED, error='boom'),
        )

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            result = await get_job(_ctx(), job.id)
        assert 'completed' in result
        assert 'failed' in result

    @pytest.mark.asyncio
    async def test_update_job(self):
        from marcel_core.jobs import load_job, save_job
        from marcel_core.jobs.models import JobDefinition, TriggerSpec
        from marcel_core.jobs.tool import update_job

        job = JobDefinition(
            name='old name',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.CRON, cron='0 7 * * *'),
            system_prompt='old',
            task='old task',
        )
        save_job(job)

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await update_job(
                _ctx(),
                job.id,
                name='new name',
                status='paused',
                cron='0 8 * * *',
                notify='silent',
                model='anthropic:claude-opus-4-6',
                timeout_minutes=20.0,
                task='new task',
                system_prompt='new prompt',
                interval_hours=2.0,
            )
        assert 'updated' in result

        reloaded = load_job(job.id)
        assert reloaded is not None
        assert reloaded.name == 'new name'
        assert reloaded.status == JobStatus.PAUSED
        assert reloaded.trigger.cron == '0 8 * * *'

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        from marcel_core.jobs.tool import update_job

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await update_job(_ctx(), 'no-such-job', name='x')
        assert 'not found' in result

    @pytest.mark.asyncio
    async def test_delete_job(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.models import JobDefinition, TriggerSpec
        from marcel_core.jobs.tool import delete_job

        job = JobDefinition(
            name='to-delete',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='x',
            task='x',
        )
        save_job(job)

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await delete_job(_ctx(), job.id)
        assert 'deleted' in result

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        from marcel_core.jobs.tool import delete_job

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await delete_job(_ctx(), 'nope')
        assert 'not found' in result


# ---------------------------------------------------------------------------
# Create with different trigger types
# ---------------------------------------------------------------------------


class TestCreateVariations:
    @pytest.mark.asyncio
    async def test_interval_trigger(self):
        from marcel_core.jobs.tool import create_job

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            result = await create_job(
                _ctx(),
                name='Sync',
                task='sync data',
                trigger_type='interval',
                system_prompt='sync worker',
                interval_hours=8.0,
                template='sync',
                skills=['banking.sync'],
                timeout_minutes=5.0,
                channel='telegram',
            )
        assert 'Sync' in result

    @pytest.mark.asyncio
    async def test_event_trigger(self):
        from marcel_core.jobs.tool import create_job

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            result = await create_job(
                _ctx(),
                name='After-sync check',
                task='check results',
                trigger_type='event',
                system_prompt='checker',
                after_job='abc123',
            )
        assert 'After-sync check' in result
        assert 'on event' in result

    @pytest.mark.asyncio
    async def test_invalid_notify_defaults(self):
        from marcel_core.jobs import list_jobs
        from marcel_core.jobs.tool import create_job

        with patch('marcel_core.jobs.scheduler.scheduler') as mock_sched:
            mock_sched._schedule = {}
            await create_job(
                _ctx(),
                name='bad-notify',
                task='t',
                trigger_type='oneshot',
                system_prompt='t',
                notify='INVALID',
            )
        jobs = list_jobs('alice')
        assert len(jobs) == 1
        # Should default to ON_OUTPUT
        from marcel_core.jobs.models import NotifyPolicy

        assert jobs[0].notify == NotifyPolicy.ON_OUTPUT


# ---------------------------------------------------------------------------
# No jobs scenario
# ---------------------------------------------------------------------------


class TestNoJobs:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        from marcel_core.jobs.tool import list_jobs

        with patch('marcel_core.jobs.scheduler.scheduler'):
            result = await list_jobs(_ctx())
        assert 'No background jobs' in result


# ---------------------------------------------------------------------------
# Templates tool
# ---------------------------------------------------------------------------


class TestJobTemplatesTool:
    @pytest.mark.asyncio
    async def test_templates(self, tmp_path, monkeypatch):
        # Templates are disk-backed since ISSUE-a7d69a. Isolate the zoo and
        # write a couple of fake habitats into the data-root so the tool
        # renders what we expect.
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_zoo_dir', None, raising=False)
        for name in ('sync', 'digest', 'scrape'):
            habitat = tmp_path / 'jobs' / name
            habitat.mkdir(parents=True)
            (habitat / 'template.yaml').write_text(
                f'description: fake {name}\n'
                'default_trigger: {type: event}\n'
                'system_prompt: s\n'
                'notify: silent\n'
                'model: anthropic:claude-haiku-4-5-20251001\n'
            )

        from marcel_core.jobs.tool import job_templates

        result = await job_templates(_ctx())
        assert 'sync' in result
        assert 'digest' in result
        assert 'scrape' in result


# ---------------------------------------------------------------------------
# Cache tools
# ---------------------------------------------------------------------------


class TestCacheTools:
    @pytest.mark.asyncio
    async def test_write_and_read_json(self):
        from marcel_core.jobs.tool import job_cache_read, job_cache_write

        result = await job_cache_write(_ctx(), 'news', '{"articles": [1, 2]}')
        assert 'Cached' in result

        data = await job_cache_read(_ctx(), 'news')
        assert 'articles' in data

    @pytest.mark.asyncio
    async def test_write_plain_string(self):
        from marcel_core.jobs.tool import job_cache_read, job_cache_write

        result = await job_cache_write(_ctx(), 'note', 'not json')
        assert 'Cached' in result

        data = await job_cache_read(_ctx(), 'note')
        assert 'not json' in data

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        from marcel_core.jobs.tool import job_cache_read

        result = await job_cache_read(_ctx(), 'missing')
        assert 'No cached data' in result


# ---------------------------------------------------------------------------
# Run now
# ---------------------------------------------------------------------------


class TestRunNow:
    @pytest.mark.asyncio
    async def test_run_nonexistent(self):
        from marcel_core.jobs.tool import run_job_now

        result = await run_job_now(_ctx(), 'no-job')
        assert 'not found' in result

    @pytest.mark.asyncio
    async def test_run_queues_job(self):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.models import JobDefinition, TriggerSpec
        from marcel_core.jobs.tool import run_job_now

        job = JobDefinition(
            name='manual-test',
            users=['alice'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            system_prompt='t',
            task='t',
        )
        save_job(job)

        with (
            patch('marcel_core.jobs.executor.execute_job_with_retries', new_callable=AsyncMock),
            patch('marcel_core.jobs.scheduler.scheduler'),
        ):
            result = await run_job_now(_ctx(), job.id)
        assert 'queued' in result
