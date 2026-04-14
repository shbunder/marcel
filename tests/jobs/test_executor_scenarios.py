"""Scenario-based tests for jobs/executor.py.

Covers: _load_job_memories, _resolve_job_skills, _build_job_context,
execute_job, execute_job_with_retries, and _notify_if_needed through
realistic job execution scenarios with mocked agents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.jobs.models import (
    JobDefinition,
    JobRun,
    NotifyPolicy,
    RunStatus,
    TriggerSpec,
    TriggerType,
)
from marcel_core.storage import _root


def _make_job(user: str = 'alice', **kw) -> JobDefinition:
    return JobDefinition(
        name=kw.pop('name', 'test-job'),
        user_slug=user,
        trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
        system_prompt=kw.pop('system_prompt', 'You are a worker.'),
        task=kw.pop('task', 'Do the work.'),
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# _load_job_memories
# ---------------------------------------------------------------------------


class TestLoadJobMemories:
    def test_returns_empty_when_no_memories(self):
        from marcel_core.jobs.executor import _load_job_memories

        result = _load_job_memories('alice')
        assert result == ''

    def test_loads_preference_and_feedback_memories(self, tmp_path):
        from marcel_core.jobs.executor import _load_job_memories

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('# Memory Index\n- [coffee](coffee.md)\n')
        (mem_dir / 'coffee.md').write_text(
            '---\nname: coffee\ndescription: prefers latte\ntype: preference\n---\nAlice prefers lattes.\n'
        )

        result = _load_job_memories('alice')
        assert 'User preferences' in result
        assert 'lattes' in result


# ---------------------------------------------------------------------------
# _resolve_job_skills
# ---------------------------------------------------------------------------


class TestResolveJobSkills:
    def test_resolves_dotted_skill_refs(self):
        from marcel_core.jobs.executor import _resolve_job_skills

        job = _make_job(skills=['banking.sync', 'banking.balance'])
        mock_skill = MagicMock()
        mock_skill.name = 'banking'
        with patch('marcel_core.skills.loader.load_skills', return_value=[mock_skill]):
            skills = _resolve_job_skills(job)
        assert len(skills) == 1
        assert skills[0].name == 'banking'

    def test_missing_skills_ignored(self):
        from marcel_core.jobs.executor import _resolve_job_skills

        job = _make_job(skills=['nonexistent.action'])
        with patch('marcel_core.skills.loader.load_skills', return_value=[]):
            skills = _resolve_job_skills(job)
        assert skills == []


# ---------------------------------------------------------------------------
# _build_job_context
# ---------------------------------------------------------------------------


class TestBuildJobContext:
    def test_builds_context_with_skills_and_creds(self, tmp_path):
        from marcel_core.jobs.executor import _build_job_context

        # Set up credentials that match job text
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('MY_API_KEY=secret123\n')

        job = _make_job(
            system_prompt='Use MY_API_KEY to authenticate.',
            task='Run with MY_API_KEY.',
            skills=['test.action'],
        )

        mock_skill = MagicMock()
        mock_skill.name = 'test'
        mock_skill.is_setup = False
        mock_skill.content = 'Skill docs here'
        mock_skill.credential_keys = {'MY_API_KEY'}

        with (
            patch('marcel_core.skills.loader.load_skills', return_value=[mock_skill]),
            patch('marcel_core.harness.context.load_channel_prompt', return_value='Deliver via Telegram.'),
        ):
            context = _build_job_context(job)

        assert 'Use MY_API_KEY' in context
        assert 'secret123' in context
        assert 'Channel' in context
        assert 'Skill docs here' in context

    def test_skips_setup_skills(self):
        from marcel_core.jobs.executor import _build_job_context

        job = _make_job(skills=['unconfigured.x'])
        mock_skill = MagicMock()
        mock_skill.name = 'unconfigured'
        mock_skill.is_setup = True
        mock_skill.content = 'Setup instructions'
        mock_skill.credential_keys = set()

        with (
            patch('marcel_core.skills.loader.load_skills', return_value=[mock_skill]),
            patch('marcel_core.harness.context.load_channel_prompt', return_value=''),
        ):
            context = _build_job_context(job)

        assert 'Setup instructions' not in context

    @pytest.mark.parametrize(
        ('policy', 'marker'),
        [
            (NotifyPolicy.SILENT, 'silent'),
            (NotifyPolicy.ON_FAILURE, 'only alerts the user on failure'),
            (NotifyPolicy.ON_OUTPUT, 'delivers its output to the user automatically'),
            (NotifyPolicy.ALWAYS, 'always delivers a message'),
        ],
    )
    def test_delivery_policy_block_injected(self, policy, marker):
        from marcel_core.jobs.executor import _build_job_context

        job = _make_job(notify=policy)
        with (
            patch('marcel_core.skills.loader.load_skills', return_value=[]),
            patch('marcel_core.harness.context.load_channel_prompt', return_value='ch'),
        ):
            context = _build_job_context(job)

        assert '## Delivery policy' in context
        assert marker in context


# ---------------------------------------------------------------------------
# execute_job
# ---------------------------------------------------------------------------


class TestExecuteJob:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        from marcel_core.jobs import read_runs
        from marcel_core.jobs.executor import execute_job

        job = _make_job()
        from marcel_core.jobs import save_job

        save_job(job)

        mock_result = MagicMock()
        mock_result.output = 'Job done successfully'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            run = await execute_job(job, 'scheduled')

        assert run.status == RunStatus.COMPLETED
        assert run.output == 'Job done successfully'

        # Check run was persisted
        runs = read_runs('alice', job.id)
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        import asyncio

        from marcel_core.jobs.executor import execute_job

        job = _make_job(timeout_seconds=1)
        from marcel_core.jobs import save_job

        save_job(job)

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)

        mock_agent = MagicMock()
        mock_agent.run = slow_run

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            run = await execute_job(job, 'scheduled')

        assert run.status == RunStatus.TIMED_OUT
        assert run.error is not None and 'timed out' in run.error

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        from marcel_core.jobs.executor import execute_job

        job = _make_job()
        from marcel_core.jobs import save_job

        save_job(job)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError('Connection refused: ECONNREFUSED'))

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            run = await execute_job(job, 'manual')

        assert run.status == RunStatus.FAILED
        assert run.error_category == 'network'

    @pytest.mark.asyncio
    async def test_usage_limits_applied(self):
        from marcel_core.jobs.executor import execute_job

        job = _make_job(request_limit=5)
        from marcel_core.jobs import save_job

        save_job(job)

        mock_result = MagicMock()
        mock_result.output = 'done'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            run = await execute_job(job, 'scheduled')

        assert run.status == RunStatus.COMPLETED
        # Verify usage_limits was passed
        call_kwargs = mock_agent.run.call_args
        assert call_kwargs.kwargs.get('usage_limits') is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('policy', 'expected'),
        [
            (NotifyPolicy.SILENT, True),
            (NotifyPolicy.ON_FAILURE, True),
            (NotifyPolicy.ON_OUTPUT, False),
            (NotifyPolicy.ALWAYS, False),
        ],
    )
    async def test_suppress_notify_wired_from_policy(self, policy, expected):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.executor import execute_job

        job = _make_job(notify=policy)
        save_job(job)

        mock_result = MagicMock()
        mock_result.output = 'done'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            await execute_job(job, 'scheduled')

        deps = mock_agent.run.call_args.kwargs['deps']
        assert deps.turn.suppress_notify is expected

    @pytest.mark.asyncio
    async def test_read_skills_primed_from_job_skills(self, tmp_path, monkeypatch):
        """ISSUE-077: the job path must prime ``turn.read_skills`` with the
        skills it's about to inject into the system prompt, so the integration
        tool's auto-loader doesn't duplicate the SkillDoc on every tool call.

        The runner primes from conversation history; jobs have no history, so
        they seed from the job definition instead.
        """
        from marcel_core.jobs import save_job
        from marcel_core.jobs.executor import execute_job
        from marcel_core.skills.loader import SkillDoc

        job = _make_job(skills=['icloud.calendar', 'banking.balance'])
        save_job(job)

        fake_skills = [
            SkillDoc(
                name='icloud',
                description='iCloud integration',
                content='# icloud',
                source='default',
                credential_keys=[],
                is_setup=False,
            ),
            SkillDoc(
                name='banking',
                description='Banking integration',
                content='# banking',
                source='default',
                credential_keys=[],
                is_setup=False,
            ),
        ]

        mock_result = MagicMock()
        mock_result.output = 'done'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', return_value=mock_agent),
            patch('marcel_core.jobs.executor._resolve_job_skills', return_value=fake_skills),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            await execute_job(job, 'scheduled')

        deps = mock_agent.run.call_args.kwargs['deps']
        # Both skill families should be marked as already-loaded so the
        # integration tool's auto-loader treats them as already-visible.
        assert 'icloud' in deps.turn.read_skills
        assert 'banking' in deps.turn.read_skills


# ---------------------------------------------------------------------------
# execute_job_with_retries
# ---------------------------------------------------------------------------


class TestExecuteJobWithRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_error(self):
        from marcel_core.jobs.executor import execute_job_with_retries

        job = _make_job(max_retries=2, backoff_schedule=[0])
        from marcel_core.jobs import save_job

        save_job(job)

        call_count = 0

        async def mock_execute(j, reason='scheduled'):
            nonlocal call_count
            call_count += 1
            run = JobRun(job_id=j.id)
            if call_count < 3:
                run.status = RunStatus.FAILED
                run.error = 'rate limit exceeded (429)'
                run.error_category = 'rate_limit'
            else:
                run.status = RunStatus.COMPLETED
                run.output = 'success'
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.user_slug, j.id, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            run = await execute_job_with_retries(job)

        assert run.status == RunStatus.COMPLETED
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self):
        from marcel_core.jobs.executor import execute_job_with_retries

        job = _make_job(max_retries=3, backoff_schedule=[0])
        from marcel_core.jobs import save_job

        save_job(job)

        async def mock_execute(j, reason='scheduled'):
            run = JobRun(job_id=j.id, status=RunStatus.FAILED, error='Invalid API key')
            run.error_category = 'permanent'
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.user_slug, j.id, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_tracks_consecutive_errors(self):
        from marcel_core.jobs import load_job
        from marcel_core.jobs.executor import execute_job_with_retries

        job = _make_job(max_retries=0)
        from marcel_core.jobs import save_job

        save_job(job)

        async def mock_execute(j, reason='scheduled'):
            run = JobRun(job_id=j.id, status=RunStatus.FAILED, error='boom')
            run.error_category = 'permanent'
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.user_slug, j.id, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            await execute_job_with_retries(job)

        reloaded = load_job('alice', job.id)
        assert reloaded is not None
        assert reloaded.consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_clears_errors_on_success(self):
        from marcel_core.jobs import load_job
        from marcel_core.jobs.executor import execute_job_with_retries

        job = _make_job(max_retries=0, consecutive_errors=5)
        from marcel_core.jobs import save_job

        save_job(job)

        async def mock_execute(j, reason='scheduled'):
            run = JobRun(job_id=j.id, status=RunStatus.COMPLETED, output='ok')
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.user_slug, j.id, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            await execute_job_with_retries(job)

        reloaded = load_job('alice', job.id)
        assert reloaded is not None
        assert reloaded.consecutive_errors == 0


# ---------------------------------------------------------------------------
# _notify_if_needed
# ---------------------------------------------------------------------------


class TestNotifyIfNeeded:
    @pytest.mark.asyncio
    async def test_skips_when_agent_already_notified(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ALWAYS)
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, agent_notified=True)
        status, error = await _notify_if_needed(job, run)
        assert status == 'skipped'

    @pytest.mark.asyncio
    async def test_always_policy_sends(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ALWAYS, channel='log')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='Hello!')
        status, error = await _notify_if_needed(job, run)
        assert status == 'sent'

    @pytest.mark.asyncio
    async def test_on_failure_respects_cooldown(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(
            notify=NotifyPolicy.ON_FAILURE,
            consecutive_errors=5,
            alert_after_consecutive_failures=3,
            alert_cooldown_seconds=3600,
            channel='log',
        )
        job.last_failure_alert_at = datetime.now(UTC)  # just alerted
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='boom')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'skipped'

    @pytest.mark.asyncio
    async def test_on_failure_sends_after_threshold(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(
            notify=NotifyPolicy.ON_FAILURE,
            consecutive_errors=3,
            alert_after_consecutive_failures=3,
            channel='log',
        )
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='something broke')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'sent'

    @pytest.mark.asyncio
    async def test_on_failure_below_threshold(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(
            notify=NotifyPolicy.ON_FAILURE,
            consecutive_errors=1,
            alert_after_consecutive_failures=3,
            channel='log',
        )
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='boom')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'skipped'

    @pytest.mark.asyncio
    async def test_on_output_sends_when_output(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ON_OUTPUT, channel='log')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='Here are results')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'sent'

    @pytest.mark.asyncio
    async def test_on_output_skips_empty(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ON_OUTPUT, channel='log')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'skipped'

    @pytest.mark.asyncio
    async def test_silent_never_sends(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.SILENT)
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='lots of output')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'skipped'

    @pytest.mark.asyncio
    async def test_timed_out_message(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ALWAYS, channel='log', timeout_seconds=300)
        run = JobRun(job_id=job.id, status=RunStatus.TIMED_OUT)
        status, _ = await _notify_if_needed(job, run)
        assert status == 'sent'

    @pytest.mark.asyncio
    async def test_telegram_notification(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ALWAYS, channel='telegram')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='Done!')

        with patch('marcel_core.jobs.executor._notify_telegram', new_callable=AsyncMock):
            status, _ = await _notify_if_needed(job, run)
        assert status == 'sent'

    @pytest.mark.asyncio
    async def test_telegram_notification_failure(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(notify=NotifyPolicy.ALWAYS, channel='telegram')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='Done!')

        with patch(
            'marcel_core.jobs.executor._notify_telegram',
            new_callable=AsyncMock,
            side_effect=RuntimeError('no chat'),
        ):
            status, error = await _notify_if_needed(job, run)
        assert status == 'failed'
        assert error is not None

    @pytest.mark.asyncio
    async def test_failure_with_consecutive_errors_in_message(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(
            notify=NotifyPolicy.ON_FAILURE,
            consecutive_errors=5,
            alert_after_consecutive_failures=1,
            channel='log',
        )
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='timeout')
        status, _ = await _notify_if_needed(job, run)
        assert status == 'sent'
