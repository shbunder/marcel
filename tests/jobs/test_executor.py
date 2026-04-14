"""Tests for the job executor hardening features.

Covers: transient error classification, exponential backoff delay computation,
timeout handling, and the ISSUE-070 local-LLM fallback path.
"""

from __future__ import annotations

import pytest

from marcel_core.config import settings
from marcel_core.jobs import executor as executor_module
from marcel_core.jobs.executor import classify_error, execute_job_with_retries
from marcel_core.jobs.models import JobDefinition, JobRun, RunStatus, TriggerSpec, TriggerType

# ---------------------------------------------------------------------------
# Transient error classification
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_rate_limit(self):
        is_transient, cat = classify_error('Rate limit exceeded (429)')
        assert is_transient is True
        assert cat == 'rate_limit'

    def test_rate_limit_variant(self):
        is_transient, cat = classify_error('too many requests')
        assert is_transient is True
        assert cat == 'rate_limit'

    def test_timeout(self):
        is_transient, cat = classify_error('Request timed out after 30s')
        assert is_transient is True
        assert cat == 'timeout'

    def test_network(self):
        is_transient, cat = classify_error('Connection refused: ECONNREFUSED')
        assert is_transient is True
        assert cat == 'network'

    def test_dns(self):
        is_transient, cat = classify_error('DNS resolution failed for api.anthropic.com')
        assert is_transient is True
        assert cat == 'network'

    def test_server_error_500(self):
        is_transient, cat = classify_error('Internal server error (500)')
        assert is_transient is True
        assert cat == 'server_error'

    def test_server_error_502(self):
        is_transient, cat = classify_error('502 Bad Gateway')
        assert is_transient is True
        assert cat == 'server_error'

    def test_overloaded(self):
        is_transient, cat = classify_error('API is overloaded, please retry')
        assert is_transient is True
        assert cat == 'server_error'

    def test_auth_quota_invalid_key(self):
        # Auth failures are not transient (retrying won't help), but they
        # are a valid trigger for the local-LLM fallback path (ISSUE-070).
        is_transient, cat = classify_error('Invalid API key: authentication failed')
        assert is_transient is False
        assert cat == 'auth_or_quota'

    def test_auth_quota_401(self):
        is_transient, cat = classify_error('HTTP 401 Unauthorized')
        assert is_transient is False
        assert cat == 'auth_or_quota'

    def test_auth_quota_insufficient_quota(self):
        is_transient, cat = classify_error('insufficient_quota: credit balance too low')
        assert is_transient is False
        assert cat == 'auth_or_quota'

    def test_permanent_not_found(self):
        is_transient, cat = classify_error("Skill 'foo' not found")
        assert is_transient is False
        assert cat == 'permanent'

    def test_permanent_validation(self):
        is_transient, cat = classify_error('ValidationError: field required')
        assert is_transient is False
        assert cat == 'permanent'

    def test_empty_string(self):
        is_transient, cat = classify_error('')
        assert is_transient is False
        assert cat == 'permanent'


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------


class TestBackoffSchedule:
    def test_default_schedule(self):
        from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType

        job = JobDefinition(
            name='test',
            user_slug='test',
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='test',
            task='test',
        )
        assert job.backoff_schedule == [30, 60, 300, 900, 3600]

    def test_backoff_index_clamped(self):
        """When attempt exceeds schedule length, last entry is used."""
        schedule = [30, 60, 300, 900, 3600]
        for attempt in (0, 1, 2, 3, 4, 10, 100):
            idx = min(attempt, len(schedule) - 1)
            assert 0 <= idx < len(schedule)
            assert schedule[idx] == schedule[min(attempt, 4)]


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


class TestModelDefaults:
    def test_timeout_default(self):
        from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType

        job = JobDefinition(
            name='test',
            user_slug='test',
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='test',
            task='test',
        )
        assert job.timeout_seconds == 600

    def test_timed_out_status_exists(self):
        from marcel_core.jobs.models import RunStatus

        assert RunStatus.TIMED_OUT.value == 'timed_out'

    def test_consecutive_errors_default(self):
        from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType

        job = JobDefinition(
            name='test',
            user_slug='test',
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='test',
            task='test',
        )
        assert job.consecutive_errors == 0
        assert job.last_error_at is None
        assert job.schedule_errors == 0
        assert job.alert_after_consecutive_failures == 3
        assert job.alert_cooldown_seconds == 3600
        assert job.retention_days == 30

    def test_run_delivery_fields(self):
        from marcel_core.jobs.models import JobRun

        run = JobRun(job_id='test')
        assert run.delivery_status is None
        assert run.delivery_error is None
        assert run.error_category is None
        assert run.fallback_used is None

    def test_allow_local_fallback_default_off(self):
        job = JobDefinition(
            name='test',
            user_slug='test',
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='test',
            task='test',
        )
        assert job.allow_local_fallback is False


# ---------------------------------------------------------------------------
# Local LLM fallback (ISSUE-070)
# ---------------------------------------------------------------------------


def _make_job(**overrides: object) -> JobDefinition:
    base: dict[str, object] = {
        'name': 'test',
        'user_slug': 'test',
        'trigger': TriggerSpec(type=TriggerType.ONESHOT),
        'system_prompt': 'prompt',
        'task': 'task',
        'model': 'anthropic:claude-sonnet-4-6',
        'max_retries': 0,
    }
    base.update(overrides)
    return JobDefinition.model_validate(base)


@pytest.fixture
def patched_side_effects(monkeypatch):
    """Stub out ``save_job``, ``append_run``, and ``_notify_if_needed`` so tests
    don't touch the filesystem or the network. Yields a list we can fill with
    the fake run(s) that ``execute_job`` should return, in order."""

    scripted_runs: list[JobRun] = []
    call_log: list[str] = []

    async def fake_execute_job(job, trigger_reason='scheduled'):
        call_log.append(job.model)
        if not scripted_runs:
            raise AssertionError('execute_job called more times than scripted')
        return scripted_runs.pop(0)

    async def fake_notify(job, run):
        return 'skipped', None

    def fake_save_job(job):
        return None

    def fake_append_run(user_slug, job_id, run):
        return None

    monkeypatch.setattr(executor_module, 'execute_job', fake_execute_job)
    monkeypatch.setattr(executor_module, '_notify_if_needed', fake_notify)
    monkeypatch.setattr('marcel_core.jobs.save_job', fake_save_job, raising=False)
    monkeypatch.setattr('marcel_core.jobs.append_run', fake_append_run, raising=False)
    return scripted_runs, call_log


class TestLocalFallback:
    @pytest.mark.asyncio
    async def test_fires_on_auth_quota_when_allowed(self, monkeypatch, patched_side_effects):
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        # First run: cloud fails with 401 → auth_or_quota (not retried).
        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='401 unauthorized', error_category='auth_or_quota')
        )
        # Second run (fallback): local succeeds.
        scripted_runs.append(JobRun(job_id='x', status=RunStatus.COMPLETED, output='hello from local'))

        job = _make_job(allow_local_fallback=True)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.COMPLETED
        assert run.output == 'hello from local'
        assert run.fallback_used == 'local'
        # Call sequence: cloud first, then local:*.
        assert call_log == ['anthropic:claude-sonnet-4-6', 'local:qwen3.5:4b']
        # Persisted model must not be mutated.
        assert job.model == 'anthropic:claude-sonnet-4-6'

    @pytest.mark.asyncio
    async def test_not_fired_when_flag_off(self, monkeypatch, patched_side_effects):
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='401 unauthorized', error_category='auth_or_quota')
        )

        job = _make_job(allow_local_fallback=False)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used is None
        assert call_log == ['anthropic:claude-sonnet-4-6']

    @pytest.mark.asyncio
    async def test_not_fired_when_local_unconfigured(self, monkeypatch, patched_side_effects):
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        monkeypatch.setattr(settings, 'marcel_local_llm_model', None)

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='401 unauthorized', error_category='auth_or_quota')
        )

        job = _make_job(allow_local_fallback=True)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used is None
        assert call_log == ['anthropic:claude-sonnet-4-6']

    @pytest.mark.asyncio
    async def test_not_fired_on_permanent_error(self, monkeypatch, patched_side_effects):
        """Permanent (non-fallback-eligible) errors should not trigger fallback."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='bad arguments', error_category='permanent')
        )

        job = _make_job(allow_local_fallback=True)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used is None
        assert call_log == ['anthropic:claude-sonnet-4-6']

    @pytest.mark.asyncio
    async def test_fires_once_only(self, monkeypatch, patched_side_effects):
        """If the local model ALSO fails, we don't recurse — one fallback attempt."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='401 unauthorized', error_category='auth_or_quota')
        )
        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='connection refused', error_category='network')
        )

        job = _make_job(allow_local_fallback=True)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used == 'local'
        assert call_log == ['anthropic:claude-sonnet-4-6', 'local:qwen3.5:4b']
        assert len(scripted_runs) == 0  # both scripted runs consumed


# ---------------------------------------------------------------------------
# ISSUE-076: fallback chain integration with jobs
# ---------------------------------------------------------------------------


class TestFallbackChain:
    """Chain-mode tests for execute_job_with_retries (ISSUE-076)."""

    @pytest.mark.asyncio
    async def test_backup_tier_tried_before_local(self, monkeypatch, patched_side_effects):
        """With MARCEL_BACKUP_MODEL set, a failing cloud primary escalates
        to the cloud backup before the local tier 3."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_fallback_model', None)
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        # Tier 1 fails with overloaded, tier 2 (gpt-4o) succeeds
        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='Overloaded', error_category='server_error')
        )
        scripted_runs.append(JobRun(job_id='x', status=RunStatus.COMPLETED, output='hello from backup'))

        job = _make_job(allow_local_fallback=True)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.COMPLETED
        assert run.fallback_used == 'backup'
        assert call_log == ['anthropic:claude-sonnet-4-6', 'openai:gpt-4o']

    @pytest.mark.asyncio
    async def test_fallback_used_names_cloud_backup_tier(self, monkeypatch, patched_side_effects):
        """When tier 2 is a cloud model, fallback_used reports 'backup', not 'local'."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='rate limit', error_category='rate_limit')
        )
        scripted_runs.append(JobRun(job_id='x', status=RunStatus.COMPLETED, output='ok'))

        run = await execute_job_with_retries(_make_job())

        assert run.fallback_used == 'backup'
        assert call_log[-1] == 'openai:gpt-4o'

    @pytest.mark.asyncio
    async def test_allow_fallback_chain_false_pins_job(self, monkeypatch, patched_side_effects):
        """Even with MARCEL_BACKUP_MODEL set, a job with allow_fallback_chain=False
        never escalates — it stays on its primary model with retries only."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='Overloaded', error_category='server_error')
        )

        job = _make_job(allow_fallback_chain=False)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used is None
        # Only tier 1 was tried — no escalation to openai:gpt-4o
        assert call_log == ['anthropic:claude-sonnet-4-6']

    @pytest.mark.asyncio
    async def test_local_pinned_job_without_opt_out_escalates(self, monkeypatch, patched_side_effects):
        """Documents the footgun: job.model='local:...' + default allow_fallback_chain=True
        DOES silently escalate to cloud. Users who pin to local must set
        allow_fallback_chain=False manually."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='Overloaded', error_category='server_error')
        )
        scripted_runs.append(JobRun(job_id='x', status=RunStatus.COMPLETED, output='cloud saved us'))

        job = _make_job(model='local:qwen3.5:4b')
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.COMPLETED
        # Chain escalated local → cloud. This is the documented footgun.
        assert call_log == ['local:qwen3.5:4b', 'openai:gpt-4o']

    @pytest.mark.asyncio
    async def test_local_pinned_job_with_opt_out_stays_local(self, monkeypatch, patched_side_effects):
        """The recommended config: local primary + allow_fallback_chain=False."""
        scripted_runs, call_log = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='Overloaded', error_category='server_error')
        )

        job = _make_job(model='local:qwen3.5:4b', allow_fallback_chain=False)
        run = await execute_job_with_retries(job)

        assert run.status == RunStatus.FAILED
        assert run.fallback_used is None
        # Never escalated
        assert call_log == ['local:qwen3.5:4b']

    @pytest.mark.asyncio
    async def test_persisted_job_model_unchanged_after_escalation(self, monkeypatch, patched_side_effects):
        """The chain swaps job.model per tier, but the original value must
        be restored before return so the persisted definition never changes."""
        scripted_runs, _ = patched_side_effects
        monkeypatch.setattr(settings, 'marcel_backup_model', 'openai:gpt-4o')

        scripted_runs.append(
            JobRun(job_id='x', status=RunStatus.FAILED, error='Overloaded', error_category='server_error')
        )
        scripted_runs.append(JobRun(job_id='x', status=RunStatus.COMPLETED, output='ok'))

        job = _make_job()
        await execute_job_with_retries(job)

        assert job.model == 'anthropic:claude-sonnet-4-6'


class TestAllowFallbackChainDefault:
    def test_default_is_true(self):
        job = JobDefinition(
            name='t',
            user_slug='t',
            trigger=TriggerSpec(type=TriggerType.ONESHOT),
            system_prompt='x',
            task='y',
        )
        assert job.allow_fallback_chain is True
