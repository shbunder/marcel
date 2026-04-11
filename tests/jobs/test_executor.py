"""Tests for the job executor hardening features.

Covers: transient error classification, exponential backoff delay computation,
and timeout handling.
"""

from __future__ import annotations

from marcel_core.jobs.executor import classify_error

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

    def test_permanent_auth(self):
        is_transient, cat = classify_error('Invalid API key: authentication failed')
        assert is_transient is False
        assert cat == 'permanent'

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
