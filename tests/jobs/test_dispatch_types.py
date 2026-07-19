"""Tests for ISSUE-ea6d47 — ``JobDefinition.dispatch_type`` and the three
executor dispatch paths (``tool`` / ``subagent`` / ``agent``).

Each path is covered in isolation:

- The pydantic ``model_validator`` on :class:`JobDefinition` enforces
  shape consistency per ``dispatch_type``.
- ``_fire_tool_job`` calls the connector tool directly and never
  touches the LLM chain.
- ``_fire_subagent_job`` loads a subagent markdown and spawns a scoped
  pydantic-ai agent, mirroring the flow in
  :mod:`marcel_core.tools.delegate`.
- ``_fire_agent_job`` is the historical path; coverage here only
  asserts that the top-level dispatcher routes to it when
  ``dispatch_type`` is omitted or explicitly ``'agent'``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from marcel_core.jobs.executor import (
    _fire_subagent_job,
    _fire_tool_job,
)
from marcel_core.jobs.models import (
    JobDefinition,
    JobDispatchType,
    RunStatus,
    TriggerSpec,
    TriggerType,
)


def _make_job(**overrides) -> JobDefinition:
    base: dict = {
        'name': 'test-job',
        'users': ['test'],
        'trigger': TriggerSpec(type=TriggerType.ONESHOT),
        'system_prompt': 'prompt',
        'task': 'task',
        'model': 'anthropic:claude-sonnet-4-6',
        'max_retries': 0,
    }
    base.update(overrides)
    return JobDefinition.model_validate(base)


# ---------------------------------------------------------------------------
# Validator — shape consistency per dispatch_type
# ---------------------------------------------------------------------------


class TestDispatchValidator:
    def test_agent_default_no_extra_fields(self):
        job = _make_job()
        assert job.dispatch_type is JobDispatchType.AGENT
        assert job.tool is None
        assert job.subagent is None

    def test_tool_dispatch_requires_tool_name(self):
        with pytest.raises(ValidationError) as exc:
            _make_job(dispatch_type='tool')
        assert 'requires the `tool` field' in str(exc.value)

    def test_subagent_dispatch_requires_subagent_name(self):
        with pytest.raises(ValidationError) as exc:
            _make_job(dispatch_type='subagent')
        assert 'requires the `subagent` field' in str(exc.value)

    def test_tool_dispatch_rejects_subagent_fields(self):
        with pytest.raises(ValidationError) as exc:
            _make_job(dispatch_type='tool', tool='docker.list', subagent='digest')
        assert "dispatch_type='tool'" in str(exc.value)

    def test_subagent_dispatch_rejects_tool_fields(self):
        with pytest.raises(ValidationError) as exc:
            _make_job(dispatch_type='subagent', subagent='digest', tool='docker.list')
        assert "dispatch_type='subagent'" in str(exc.value)

    def test_agent_dispatch_rejects_tool_fields(self):
        with pytest.raises(ValidationError) as exc:
            _make_job(tool='docker.list')
        assert "dispatch_type='agent'" in str(exc.value)

    def test_backcompat_no_dispatch_type_key_defaults_agent(self):
        """A Phase-1-era JobDefinition dict parses without a dispatch_type key
        and comes back as AGENT — the validator does not fail when every
        non-agent shape field is unset."""
        raw = {
            'name': 'legacy',
            'users': ['test'],
            'trigger': {'type': 'oneshot'},
            'system_prompt': 'sp',
            'task': 't',
        }
        job = JobDefinition.model_validate(raw)
        assert job.dispatch_type is JobDispatchType.AGENT
        assert 'dispatch_type' not in raw  # source stayed pristine


# ---------------------------------------------------------------------------
# _fire_tool_job
# ---------------------------------------------------------------------------


class TestFireToolJob:
    @pytest.mark.asyncio
    async def test_success_returns_handler_output(self, monkeypatch):
        calls: list[tuple[dict, str]] = []

        async def fake_call(ref: str, params: dict, slug: str, *, timeout: float = 300.0) -> str:
            assert ref == 'demo.ping'
            calls.append((params, slug))
            return 'handler output'

        monkeypatch.setattr('marcel_core.connectors.toolset.call_connector_tool', fake_call)

        job = _make_job(dispatch_type='tool', tool='demo.ping', tool_params={'x': 1})
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.COMPLETED
        assert run.output == 'handler output'
        assert calls == [({'x': 1}, 'test')]
        assert run.finished_at is not None

    @pytest.mark.asyncio
    async def test_missing_handler_fails_with_config_category(self, monkeypatch):
        async def raise_key_error(ref: str, params: dict, slug: str, *, timeout: float = 300.0) -> str:
            raise KeyError(ref)

        monkeypatch.setattr('marcel_core.connectors.toolset.call_connector_tool', raise_key_error)

        job = _make_job(dispatch_type='tool', tool='unknown.thing')
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'unknown.thing' in (run.error or '')

    @pytest.mark.asyncio
    async def test_timeout_marks_run_timed_out(self, monkeypatch):
        import asyncio

        async def slow_call(ref: str, params: dict, slug: str, *, timeout: float = 300.0) -> str:
            await asyncio.sleep(10)
            return 'never'

        monkeypatch.setattr('marcel_core.connectors.toolset.call_connector_tool', slow_call)

        # timeout_seconds=0 triggers TimeoutError deterministically
        job = _make_job(dispatch_type='tool', tool='slow.op', timeout_seconds=0)
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.TIMED_OUT
        assert run.error_category == 'timeout'
        assert '0s' in (run.error or '')

    @pytest.mark.asyncio
    async def test_handler_exception_classifies_error(self, monkeypatch):
        async def boom(ref: str, params: dict, slug: str, *, timeout: float = 300.0) -> str:
            raise RuntimeError('rate limit exceeded (429)')

        monkeypatch.setattr('marcel_core.connectors.toolset.call_connector_tool', boom)

        job = _make_job(dispatch_type='tool', tool='boom.op')
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'rate_limit'  # classify_error picks this up
        assert 'rate limit' in (run.error or '')


# ---------------------------------------------------------------------------
# _fire_subagent_job
# ---------------------------------------------------------------------------


def _make_agent_doc(**overrides) -> SimpleNamespace:
    """Minimal stand-in for :class:`marcel_core.capabilities.subagents.SubagentDoc`."""
    base = {
        'name': 'test-sub',
        'description': 'test subagent',
        'system_prompt': 'You are a test subagent.',
        'source': 'stub',
        'model': None,
        'tools': None,
        'disallowed_tools': [],
        'max_requests': None,
        'timeout_seconds': 60,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeAgent:
    """Minimal stand-in for the child agent built by ``build_child_agent``."""

    def __init__(self, output: str = 'sub output', should_raise: Exception | None = None, model=None):
        self._output = output
        self._raise = should_raise
        self.model = model

    async def run(self, prompt: str, *, deps, usage_limits=None, model=None):
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(output=self._output, prompt_seen=prompt)


class TestFireSubagentJob:
    """The SUBAGENT dispatch path rides the capability seams
    (FEAT-260718-b6d1da): ``load_agent_doc`` + ``build_child_agent`` from
    :mod:`marcel_core.capabilities.subagents`."""

    @pytest.mark.asyncio
    async def test_success_returns_agent_output(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )
        captured: dict = {}

        def fake_build(doc, *, role, cwd=None, user_slug=None, memory=True, code_mode=True):
            captured.update(doc=doc, role=role, memory=memory, code_mode=code_mode)
            return _FakeAgent(output='digest ready')

        monkeypatch.setattr('marcel_core.capabilities.subagents.build_child_agent', fake_build)

        job = _make_job(
            dispatch_type='subagent',
            subagent='digest',
            subagent_task='Summarise for {user_slug}',
        )
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.COMPLETED
        assert run.output == 'digest ready'
        # The subagent inherits role='user', not admin — jobs never escalate role.
        assert captured['role'] == 'user'
        assert captured['doc'].name == 'digest'
        # Historical parity: job subagents build lean (pre-close finding).
        assert captured['memory'] is False and captured['code_mode'] is False

    @pytest.mark.asyncio
    async def test_agent_not_found_fails_with_config_category(self, monkeypatch):
        from marcel_core.capabilities.subagents import SubagentNotFoundError

        def raise_not_found(name: str, user_slug=None):
            raise SubagentNotFoundError(f'no agent {name!r}')

        monkeypatch.setattr('marcel_core.capabilities.subagents.load_agent_doc', raise_not_found)

        job = _make_job(dispatch_type='subagent', subagent='ghost', subagent_task='do it')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'ghost' in (run.error or '')

    @pytest.mark.asyncio
    async def test_task_user_slug_placeholder_is_substituted(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )
        observed: dict = {}

        class _CapturingAgent(_FakeAgent):
            async def run(self, prompt, *, deps, usage_limits=None, model=None):
                observed['prompt'] = prompt
                return SimpleNamespace(output='ok')

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _CapturingAgent(),
        )

        job = _make_job(
            dispatch_type='subagent',
            subagent='digest',
            subagent_task='for user: {user_slug}',
        )
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.COMPLETED
        assert observed['prompt'] == 'for user: shaun'

    @pytest.mark.asyncio
    async def test_task_bad_placeholder_fails_loud(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )
        # No agent is built — the format error aborts before that.
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: pytest.fail('should not reach build_child_agent'),
        )

        job = _make_job(
            dispatch_type='subagent',
            subagent='digest',
            subagent_task='hello {nobody}',
        )
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'nobody' in (run.error or '')

    @pytest.mark.asyncio
    async def test_unresolvable_tier_sentinel_model_fails_config(self, monkeypatch):
        """If the subagent frontmatter pins an unconfigured ``tier:`` model,
        build_child_agent raises and the run is marked config-failed."""
        from marcel_core.harness.model_chain import TierNotConfigured

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name, model='tier:power'),
        )

        def raise_not_configured(doc, **kw):
            raise TierNotConfigured('power')

        monkeypatch.setattr('marcel_core.capabilities.subagents.build_child_agent', raise_not_configured)

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'model resolution failed' in (run.error or '')

    @pytest.mark.asyncio
    async def test_max_requests_becomes_usage_limits(self, monkeypatch):
        """A doc with ``max_requests`` runs the child under a UsageLimits cap."""
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name, max_requests=7),
        )
        observed: dict = {}

        class _CapturingAgent(_FakeAgent):
            async def run(self, prompt, *, deps, usage_limits=None, model=None):
                observed['usage_limits'] = usage_limits
                return SimpleNamespace(output='ok')

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _CapturingAgent(),
        )

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.COMPLETED
        assert observed['usage_limits'] is not None
        assert observed['usage_limits'].request_limit == 7

    @pytest.mark.asyncio
    async def test_inherit_model_falls_back_to_job_model(self, monkeypatch):
        """A model-less child (doc says inherit) runs on the job's own model."""
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )
        observed: dict = {}

        class _CapturingAgent(_FakeAgent):
            async def run(self, prompt, *, deps, usage_limits=None, model=None):
                observed['model'] = model
                return SimpleNamespace(output='ok')

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _CapturingAgent(model=None),
        )

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go', model='openai:gpt-4o')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.COMPLETED
        assert observed['model'] == 'openai:gpt-4o'

    @pytest.mark.asyncio
    async def test_agent_build_failure_fails_config(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )

        def boom_build(doc, **kw):
            raise RuntimeError('cannot construct agent')

        monkeypatch.setattr('marcel_core.capabilities.subagents.build_child_agent', boom_build)

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'subagent build failed' in (run.error or '')

    @pytest.mark.asyncio
    async def test_subagent_timeout_marks_timed_out(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name, timeout_seconds=0),
        )

        class _SlowAgent(_FakeAgent):
            async def run(self, prompt, *, deps, usage_limits=None, model=None):
                await asyncio.sleep(10)
                return SimpleNamespace(output='never')

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _SlowAgent(),
        )

        # effective_timeout = min(job.timeout_seconds, agent_doc.timeout_seconds=0) → 0
        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.TIMED_OUT
        assert run.error_category == 'timeout'
        assert '0s' in (run.error or '')

    @pytest.mark.asyncio
    async def test_subagent_run_exception_classified(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _FakeAgent(should_raise=RuntimeError('rate limit exceeded (429)')),
        )

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'rate_limit'
        assert 'rate limit' in (run.error or '')

    @pytest.mark.asyncio
    async def test_subagent_agent_notified_recorded(self, monkeypatch):
        """A subagent that notifies during its run marks agent_notified on the
        JobRun so the executor's auto-notify is later suppressed."""
        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.load_agent_doc',
            lambda name, user_slug=None: _make_agent_doc(name=name),
        )

        class _NotifyingAgent(_FakeAgent):
            async def run(self, prompt, *, deps, usage_limits=None, model=None):
                deps.turn.notified = True
                return SimpleNamespace(output='notified the user')

        monkeypatch.setattr(
            'marcel_core.capabilities.subagents.build_child_agent',
            lambda doc, **kw: _NotifyingAgent(),
        )

        job = _make_job(dispatch_type='subagent', subagent='digest', subagent_task='go')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.COMPLETED
        assert run.agent_notified is True
