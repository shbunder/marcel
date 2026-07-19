"""Scenario-based tests for jobs/executor.py.

Covers: _load_job_memories, _build_job_context, execute_job,
execute_job_with_retries, and _notify_if_needed through realistic job
execution scenarios with mocked agents. Scoped-assembly scenarios
(FEAT-260718-49a01a) live in test_job_scoping.py next to their zoo fixture.
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
    users = kw.pop('users', [user])
    return JobDefinition(
        name=kw.pop('name', 'test-job'),
        users=users,
        trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
        system_prompt=kw.pop('system_prompt', 'You are a worker.'),
        task=kw.pop('task', 'Do the work.'),
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# _resolve_run_user
# ---------------------------------------------------------------------------


class TestResolveRunUser:
    def test_system_scope_job_resolves_to_system_user(self):
        from marcel_core.jobs import SYSTEM_USER
        from marcel_core.jobs.executor import _resolve_run_user

        job = _make_job(users=[])
        assert _resolve_run_user(job, None) == SYSTEM_USER

    def test_single_user_job_auto_picks_sole_user(self):
        from marcel_core.jobs.executor import _resolve_run_user

        job = _make_job(users=['alice'])
        assert _resolve_run_user(job, None) == 'alice'

    def test_explicit_slug_wins(self):
        from marcel_core.jobs.executor import _resolve_run_user

        job = _make_job(users=['alice', 'bob'])
        assert _resolve_run_user(job, 'bob') == 'bob'

    def test_multi_user_job_without_slug_raises(self):
        from marcel_core.jobs.executor import _resolve_run_user

        job = _make_job(users=['alice', 'bob'])
        with pytest.raises(ValueError, match='explicit user_slug'):
            _resolve_run_user(job, None)


# ---------------------------------------------------------------------------
# _load_job_memories
# ---------------------------------------------------------------------------


class TestLoadJobMemories:
    def test_returns_empty_when_no_memories(self):
        from marcel_core.jobs.executor import _load_job_memories

        result = _load_job_memories('alice')
        assert result == ''

    def test_system_user_gets_no_memories(self):
        from marcel_core.jobs import SYSTEM_USER
        from marcel_core.jobs.executor import _load_job_memories

        assert _load_job_memories(SYSTEM_USER) == ''

    def test_empty_memory_files_produce_no_section(self, tmp_path, monkeypatch):
        """A memory whose content vanished between scan and load (or is blank)
        is skipped; if all relevant memories are blank, no section is emitted."""
        import marcel_core.storage.memory as memory_module
        from marcel_core.jobs.executor import _load_job_memories

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'coffee.md').write_text(
            '---\nname: coffee\ndescription: prefers latte\ntype: preference\n---\nAlice prefers lattes.\n'
        )
        monkeypatch.setattr(memory_module, 'load_memory_file', lambda slug, topic: '')

        assert _load_job_memories('alice') == ''

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

    def test_memory_without_name_labelled_from_topic(self, tmp_path):
        """A memory file with no ``name`` in frontmatter falls back to a label
        derived from the topic (underscores → spaces)."""
        from marcel_core.jobs.executor import _load_job_memories

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'work_style.md').write_text(
            '---\ndescription: how alice likes updates\ntype: feedback\n---\nKeep it terse.\n'
        )

        result = _load_job_memories('alice')
        assert '### [feedback] work style' in result
        assert 'Keep it terse.' in result

    def test_ignores_non_preference_memory_types(self, tmp_path):
        """Only PREFERENCE and FEEDBACK memories are injected into a job; a
        plain fact-type memory is left out."""
        from marcel_core.jobs.executor import _load_job_memories

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'trivia.md').write_text('---\nname: trivia\ntype: fact\n---\nThe sky is blue.\n')

        assert _load_job_memories('alice') == ''


# ---------------------------------------------------------------------------
# _build_job_context
# ---------------------------------------------------------------------------


class TestBuildJobContext:
    def test_legacy_context_injects_prose_matched_creds(self, tmp_path):
        from marcel_core.jobs.executor import _build_job_context

        # Set up credentials that match job text
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('MY_API_KEY=secret123\n')

        job = _make_job(
            system_prompt='Use MY_API_KEY to authenticate.',
            task='Run with MY_API_KEY.',
        )

        with patch('marcel_core.harness.context.load_channel_prompt', return_value='Deliver via Telegram.'):
            context = _build_job_context(job)

        assert 'Use MY_API_KEY' in context
        assert 'secret123' in context
        assert 'Channel' in context

    def test_scoped_context_carries_no_creds_or_memories(self, tmp_path):
        """The scoped path (FEAT-260718-49a01a): connector auth lives at the
        transport layer and memory injection is explicitly out — the prompt is
        the job's own text plus channel + delivery policy, nothing else."""
        from marcel_core.jobs.executor import _build_job_context

        user_dir = tmp_path / 'users' / 'alice'
        mem_dir = user_dir / 'memory'
        mem_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('MY_API_KEY=secret123\n')
        (mem_dir / 'style.md').write_text(
            '---\nname: style\ndescription: tone\ntype: preference\n---\nAlice likes short bullets.\n'
        )

        job = _make_job(system_prompt='Use MY_API_KEY.', task='Run with MY_API_KEY.')
        with patch('marcel_core.harness.context.load_channel_prompt', return_value='ch'):
            context = _build_job_context(job, scoped=True)

        assert 'secret123' not in context
        assert 'short bullets' not in context
        assert '## Delivery policy' in context

    def test_credential_referenced_only_in_task_text_is_injected(self, tmp_path):
        """A credential named in the job's task/system prompt (but not declared
        by any skill) is still pulled from the vault and injected."""
        from marcel_core.jobs.executor import _build_job_context

        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'credentials.env').write_text('FREEFORM_TOKEN=tok999\nUNUSED_SECRET=nope\n')

        job = _make_job(
            system_prompt='Authenticate as usual.',
            task='Use FREEFORM_TOKEN to call the API.',
        )
        with patch('marcel_core.harness.context.load_channel_prompt', return_value='ch'):
            context = _build_job_context(job)

        assert 'tok999' in context  # referenced token injected
        assert 'nope' not in context  # unreferenced secret withheld

    def test_memory_section_injected_into_context(self, tmp_path):
        """Preference/feedback memories for the run user land in the assembled
        system prompt."""
        from marcel_core.jobs.executor import _build_job_context

        mem_dir = tmp_path / 'users' / 'alice' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'style.md').write_text(
            '---\nname: style\ndescription: tone\ntype: preference\n---\nAlice likes short bullets.\n'
        )

        job = _make_job()
        with patch('marcel_core.harness.context.load_channel_prompt', return_value='ch'):
            context = _build_job_context(job)

        assert 'User preferences & feedback' in context
        assert 'short bullets' in context

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
        runs = read_runs(job.id, 'alice')
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
    async def test_legacy_unscoped_job_keeps_full_pool_and_logs_advisory(self, tmp_path, monkeypatch, caplog):
        """A pre-scoping job (no skills:/connectors:) builds exactly the
        pre-FEAT-260718-49a01a agent — no tool filter, no extra capabilities,
        skills/connectors/memory off — and each run logs a scoping advisory."""
        import logging as _logging

        from marcel_core.jobs import save_job
        from marcel_core.jobs.executor import execute_job

        job = _make_job()
        save_job(job)
        caplog.set_level(_logging.INFO, logger='marcel_core.jobs.executor')

        captured_kwargs: list = []

        def _capture_create(*args, **kwargs):
            captured_kwargs.append(kwargs)
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.output = 'done'
            mock_agent.run = AsyncMock(return_value=mock_result)
            return mock_agent

        with (
            patch('marcel_core.harness.agent.create_marcel_agent', side_effect=_capture_create),
            patch('marcel_core.jobs.executor._build_job_context', return_value='ctx'),
        ):
            await execute_job(job, 'scheduled')

        assert captured_kwargs, 'create_marcel_agent was not called'
        assert all(kw.get('skills') is False for kw in captured_kwargs)
        assert all(kw.get('tool_filter') is None for kw in captured_kwargs)
        assert all(not kw.get('extra_capabilities') for kw in captured_kwargs)
        assert any('runs unscoped' in r.message for r in caplog.records)


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

        async def mock_execute(j, reason='scheduled', *, user_slug=None):
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

            append_run(j.id, j.users[0] if j.users else None, run)
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

        async def mock_execute(j, reason='scheduled', *, user_slug=None):
            run = JobRun(job_id=j.id, status=RunStatus.FAILED, error='Invalid API key')
            run.error_category = 'permanent'
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.id, j.users[0] if j.users else None, run)
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

        async def mock_execute(j, reason='scheduled', *, user_slug=None):
            run = JobRun(job_id=j.id, status=RunStatus.FAILED, error='boom')
            run.error_category = 'permanent'
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.id, j.users[0] if j.users else None, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            await execute_job_with_retries(job)

        reloaded = load_job(job.id)
        assert reloaded is not None
        assert reloaded.consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_clears_errors_on_success(self):
        from marcel_core.jobs import load_job
        from marcel_core.jobs.executor import execute_job_with_retries

        job = _make_job(max_retries=0, consecutive_errors=5)
        from marcel_core.jobs import save_job

        save_job(job)

        async def mock_execute(j, reason='scheduled', *, user_slug=None):
            run = JobRun(job_id=j.id, status=RunStatus.COMPLETED, output='ok')
            run.finished_at = datetime.now(UTC)
            from marcel_core.jobs import append_run

            append_run(j.id, j.users[0] if j.users else None, run)
            return run

        with (
            patch('marcel_core.jobs.executor.execute_job', side_effect=mock_execute),
            patch(
                'marcel_core.jobs.executor._notify_if_needed', new_callable=AsyncMock, return_value=('skipped', None)
            ),
        ):
            await execute_job_with_retries(job)

        reloaded = load_job(job.id)
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

    @pytest.mark.asyncio
    async def test_system_scope_job_never_notifies(self):
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(users=[], notify=NotifyPolicy.ALWAYS, channel='telegram')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='result')
        status, error = await _notify_if_needed(job, run)
        assert status == 'skipped'
        assert error is None

    @pytest.mark.asyncio
    async def test_failure_message_appends_consecutive_count(self):
        """When more than one consecutive failure has piled up, the count is
        appended to the humanized error in the delivered message."""
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(
            notify=NotifyPolicy.ON_FAILURE,
            consecutive_errors=4,
            alert_after_consecutive_failures=1,
            channel='telegram',
        )
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='429 Too Many Requests')

        sent: dict = {}

        async def capture(slug, message):
            sent['slug'] = slug
            sent['message'] = message

        with patch('marcel_core.jobs.executor._notify_telegram', side_effect=capture):
            status, _ = await _notify_if_needed(job, run)

        assert status == 'sent'
        assert '4 consecutive failures' in sent['message']
        # last_failure_alert_at is stamped for cooldown tracking
        reloaded_at = job.last_failure_alert_at
        assert reloaded_at is not None

    @pytest.mark.asyncio
    async def test_failure_message_omits_count_for_single_failure(self):
        """A first (or forced) failure with consecutive_errors <= 1 delivers the
        bare humanized error, without a '(N consecutive failures)' suffix."""
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(name='Backup', notify=NotifyPolicy.ALWAYS, channel='telegram')
        run = JobRun(job_id=job.id, status=RunStatus.FAILED, error='429 Too Many Requests')

        sent: dict = {}

        async def capture(slug, message):
            sent['message'] = message

        with patch('marcel_core.jobs.executor._notify_telegram', side_effect=capture):
            status, _ = await _notify_if_needed(job, run)

        assert status == 'sent'
        assert 'consecutive failures' not in sent['message']
        assert 'Backup' in sent['message']

    @pytest.mark.asyncio
    async def test_completed_with_empty_output_uses_fallback_message(self):
        """An ALWAYS job that completes with no output still delivers a generic
        'completed' line rather than an empty message."""
        from marcel_core.jobs.executor import _notify_if_needed

        job = _make_job(name='Nightly report', notify=NotifyPolicy.ALWAYS, channel='telegram')
        run = JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='   ')

        sent: dict = {}

        async def capture(slug, message):
            sent['message'] = message

        with patch('marcel_core.jobs.executor._notify_telegram', side_effect=capture):
            status, _ = await _notify_if_needed(job, run)

        assert status == 'sent'
        assert 'Nightly report' in sent['message']
        assert 'completed' in sent['message']


# ---------------------------------------------------------------------------
# _notify_telegram
# ---------------------------------------------------------------------------


class TestNotifyTelegram:
    @pytest.mark.asyncio
    async def test_sends_via_registered_channel(self):
        from marcel_core.jobs.executor import _notify_telegram

        channel = MagicMock()
        channel.send_message = AsyncMock(return_value=True)
        with patch('marcel_core.plugin.get_channel', return_value=channel):
            await _notify_telegram('alice', 'hello')
        channel.send_message.assert_awaited_once_with('alice', 'hello')

    @pytest.mark.asyncio
    async def test_no_channel_registered_is_noop(self, caplog):
        import logging

        from marcel_core.jobs.executor import _notify_telegram

        with (
            patch('marcel_core.plugin.get_channel', return_value=None),
            caplog.at_level(logging.WARNING, logger='marcel_core.jobs.executor'),
        ):
            await _notify_telegram('alice', 'hello')
        assert 'telegram channel not registered' in caplog.text

    @pytest.mark.asyncio
    async def test_no_chat_id_logs_warning(self, caplog):
        import logging

        from marcel_core.jobs.executor import _notify_telegram

        channel = MagicMock()
        channel.send_message = AsyncMock(return_value=False)  # no chat id found
        with (
            patch('marcel_core.plugin.get_channel', return_value=channel),
            caplog.at_level(logging.WARNING, logger='marcel_core.jobs.executor'),
        ):
            await _notify_telegram('alice', 'hello')
        assert 'no Telegram chat ID found' in caplog.text

    @pytest.mark.asyncio
    async def test_exception_is_swallowed_and_logged(self, caplog):
        import logging

        from marcel_core.jobs.executor import _notify_telegram

        channel = MagicMock()
        channel.send_message = AsyncMock(side_effect=RuntimeError('telegram down'))
        with (
            patch('marcel_core.plugin.get_channel', return_value=channel),
            caplog.at_level(logging.ERROR, logger='marcel_core.jobs.executor'),
        ):
            await _notify_telegram('alice', 'hello')  # must not raise
        assert 'Telegram notification failed' in caplog.text
