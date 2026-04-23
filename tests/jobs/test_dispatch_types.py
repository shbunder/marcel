"""Tests for ISSUE-ea6d47 — ``JobDefinition.dispatch_type`` and the three
executor dispatch paths (``tool`` / ``subagent`` / ``agent``).

Each path is covered in isolation:

- The pydantic ``model_validator`` on :class:`JobDefinition` enforces
  shape consistency per ``dispatch_type``.
- ``_fire_tool_job`` calls into the toolkit registry directly and never
  touches the LLM chain.
- ``_fire_subagent_job`` loads a subagent markdown and spawns a scoped
  pydantic-ai agent, mirroring the flow in
  :mod:`marcel_core.tools.delegate`.
- ``_fire_agent_job`` is the historical path; coverage here only
  asserts that the top-level dispatcher routes to it when
  ``dispatch_type`` is omitted or explicitly ``'agent'``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from marcel_core.jobs import executor as executor_module
from marcel_core.jobs.executor import (
    _fire_subagent_job,
    _fire_tool_job,
    execute_job_with_retries,
)
from marcel_core.jobs.models import (
    JobDefinition,
    JobDispatchType,
    JobRun,
    NotifyPolicy,
    RunStatus,
    TriggerSpec,
    TriggerType,
)
from marcel_core.plugin import jobs as plugin_jobs


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

        async def fake_handler(params: dict, user_slug: str) -> str:
            calls.append((params, user_slug))
            return 'handler output'

        monkeypatch.setattr(
            'marcel_core.toolkit.get_handler',
            lambda name: fake_handler if name == 'demo.ping' else (_ for _ in ()).throw(KeyError(name)),
        )

        job = _make_job(dispatch_type='tool', tool='demo.ping', tool_params={'x': 1})
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.COMPLETED
        assert run.output == 'handler output'
        assert calls == [({'x': 1}, 'test')]
        assert run.finished_at is not None

    @pytest.mark.asyncio
    async def test_missing_handler_fails_with_config_category(self, monkeypatch):
        def raise_key_error(name: str):
            raise KeyError(name)

        monkeypatch.setattr('marcel_core.toolkit.get_handler', raise_key_error)

        job = _make_job(dispatch_type='tool', tool='unknown.thing')
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'unknown.thing' in (run.error or '')

    @pytest.mark.asyncio
    async def test_timeout_marks_run_timed_out(self, monkeypatch):
        import asyncio

        async def slow_handler(params: dict, user_slug: str) -> str:
            await asyncio.sleep(10)
            return 'never'

        monkeypatch.setattr('marcel_core.toolkit.get_handler', lambda _name: slow_handler)

        # timeout_seconds=0 triggers TimeoutError deterministically
        job = _make_job(dispatch_type='tool', tool='slow.op', timeout_seconds=0)
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.TIMED_OUT
        assert run.error_category == 'timeout'
        assert '0s' in (run.error or '')

    @pytest.mark.asyncio
    async def test_handler_exception_classifies_error(self, monkeypatch):
        async def boom(params: dict, user_slug: str) -> str:
            raise RuntimeError('rate limit exceeded (429)')

        monkeypatch.setattr('marcel_core.toolkit.get_handler', lambda _name: boom)

        job = _make_job(dispatch_type='tool', tool='boom.op')
        run = await _fire_tool_job(job, 'test')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'rate_limit'  # classify_error picks this up
        assert 'rate limit' in (run.error or '')


# ---------------------------------------------------------------------------
# _fire_subagent_job
# ---------------------------------------------------------------------------


def _make_agent_doc(**overrides) -> SimpleNamespace:
    """Minimal stand-in for :class:`marcel_core.agents.loader.AgentDoc`."""
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
    """Minimal stand-in for a pydantic-ai Agent returned by ``create_marcel_agent``."""

    def __init__(self, output: str = 'sub output', should_raise: Exception | None = None):
        self._output = output
        self._raise = should_raise

    async def run(self, prompt: str, *, deps, usage_limits=None):
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(output=self._output, prompt_seen=prompt)


class TestFireSubagentJob:
    @pytest.mark.asyncio
    async def test_success_returns_agent_output(self, monkeypatch):
        monkeypatch.setattr(
            'marcel_core.agents.loader.load_agent',
            lambda name: _make_agent_doc(name=name),
        )
        captured: dict = {}

        def fake_create(*, model, system_prompt, role, tool_filter):
            captured.update(
                model=model,
                system_prompt=system_prompt,
                role=role,
                tool_filter=tool_filter,
            )
            return _FakeAgent(output='digest ready')

        monkeypatch.setattr('marcel_core.harness.agent.create_marcel_agent', fake_create)
        monkeypatch.setattr(
            'marcel_core.tools.delegate._default_pool_minus',
            lambda role, disallowed, include_delegate: {'marcel'},
        )

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
        # tool_filter defaulted (no frontmatter tools: list).
        assert captured['tool_filter'] == {'marcel'}

    @pytest.mark.asyncio
    async def test_agent_not_found_fails_with_config_category(self, monkeypatch):
        from marcel_core.agents.loader import AgentNotFoundError

        def raise_not_found(name: str):
            raise AgentNotFoundError(f'no agent {name!r}')

        monkeypatch.setattr('marcel_core.agents.loader.load_agent', raise_not_found)

        job = _make_job(dispatch_type='subagent', subagent='ghost', subagent_task='do it')
        run = await _fire_subagent_job(job, 'test', user_slug='shaun')

        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'ghost' in (run.error or '')

    @pytest.mark.asyncio
    async def test_task_user_slug_placeholder_is_substituted(self, monkeypatch):
        monkeypatch.setattr('marcel_core.agents.loader.load_agent', lambda name: _make_agent_doc(name=name))
        observed: dict = {}

        def fake_create(*, model, system_prompt, role, tool_filter):
            agent = _FakeAgent()

            async def run(prompt, *, deps, usage_limits=None):
                observed['prompt'] = prompt
                return SimpleNamespace(output='ok')

            agent.run = run
            return agent

        monkeypatch.setattr('marcel_core.harness.agent.create_marcel_agent', fake_create)
        monkeypatch.setattr(
            'marcel_core.tools.delegate._default_pool_minus',
            lambda role, disallowed, include_delegate: set(),
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
        monkeypatch.setattr('marcel_core.agents.loader.load_agent', lambda name: _make_agent_doc(name=name))
        # No agent is built — the format error aborts before that.
        monkeypatch.setattr(
            'marcel_core.harness.agent.create_marcel_agent',
            lambda **kw: pytest.fail('should not reach create_marcel_agent'),
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


# ---------------------------------------------------------------------------
# Top-level dispatcher — ensure execute_job_with_retries routes correctly
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    @pytest.mark.asyncio
    async def test_tool_dispatch_bypasses_agent_path(self, monkeypatch):
        routed: list[str] = []

        async def fake_tool(job, trigger_reason, *, user_slug):
            routed.append('tool')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='tool-ok')

        async def fake_agent(job, trigger_reason, *, user_slug):
            routed.append('agent')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='agent-ok')

        async def fake_subagent(job, trigger_reason, *, user_slug):
            routed.append('subagent')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='subagent-ok')

        async def fake_notify(job, run, *, user_slug=None):
            return 'skipped', None

        monkeypatch.setattr(executor_module, '_fire_tool_job', fake_tool)
        monkeypatch.setattr(executor_module, '_fire_agent_job', fake_agent)
        monkeypatch.setattr(executor_module, '_fire_subagent_job', fake_subagent)
        monkeypatch.setattr(executor_module, '_notify_if_needed', fake_notify)
        monkeypatch.setattr('marcel_core.jobs.save_job', lambda job: None, raising=False)
        monkeypatch.setattr(
            'marcel_core.jobs.append_run',
            lambda job_id, user_slug, run: None,
            raising=False,
        )

        job = _make_job(dispatch_type='tool', tool='demo.ping')
        run = await execute_job_with_retries(job)
        assert routed == ['tool']
        assert run.output == 'tool-ok'

    @pytest.mark.asyncio
    async def test_default_dispatch_routes_to_agent(self, monkeypatch):
        routed: list[str] = []

        async def fake_tool(job, trigger_reason, *, user_slug):
            routed.append('tool')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED)

        async def fake_agent(job, trigger_reason, *, user_slug):
            routed.append('agent')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED, output='agent-ok')

        async def fake_subagent(job, trigger_reason, *, user_slug):
            routed.append('subagent')
            return JobRun(job_id=job.id, status=RunStatus.COMPLETED)

        async def fake_notify(job, run, *, user_slug=None):
            return 'skipped', None

        monkeypatch.setattr(executor_module, '_fire_tool_job', fake_tool)
        monkeypatch.setattr(executor_module, '_fire_agent_job', fake_agent)
        monkeypatch.setattr(executor_module, '_fire_subagent_job', fake_subagent)
        monkeypatch.setattr(executor_module, '_notify_if_needed', fake_notify)
        monkeypatch.setattr('marcel_core.jobs.save_job', lambda job: None, raising=False)
        monkeypatch.setattr(
            'marcel_core.jobs.append_run',
            lambda job_id, user_slug, run: None,
            raising=False,
        )

        job = _make_job()  # no dispatch_type → AGENT
        await execute_job_with_retries(job)
        assert routed == ['agent']


# ---------------------------------------------------------------------------
# Template schema validator (plugin/jobs.py)
# ---------------------------------------------------------------------------


def _write_template(pkg_dir: Path, body: dict) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / 'template.yaml').write_text(yaml.safe_dump(body), encoding='utf-8')


class TestTemplateDispatchSchema:
    def test_absent_dispatch_type_accepted(self, tmp_path):
        _write_template(
            tmp_path / 'demo',
            {
                'description': 'd',
                'system_prompt': 'sp',
                'notify': 'silent',
                'model': 'anthropic:claude-haiku-4-5-20251001',
            },
        )
        parsed = plugin_jobs._load_template_file(tmp_path / 'demo')
        assert parsed is not None
        assert 'dispatch_type' not in parsed  # preserved as-omitted

    def test_tool_dispatch_requires_tool_key(self, tmp_path, caplog):
        _write_template(
            tmp_path / 'rogue',
            {
                'description': 'd',
                'system_prompt': 'sp',
                'notify': 'silent',
                'model': 'anthropic:claude-haiku-4-5-20251001',
                'dispatch_type': 'tool',
            },
        )
        with caplog.at_level('ERROR'):
            parsed = plugin_jobs._load_template_file(tmp_path / 'rogue')
        assert parsed is None
        assert 'required `tool:` key' in caplog.text

    def test_subagent_dispatch_requires_subagent_key(self, tmp_path, caplog):
        _write_template(
            tmp_path / 'rogue',
            {
                'description': 'd',
                'system_prompt': 'sp',
                'notify': 'silent',
                'model': 'anthropic:claude-haiku-4-5-20251001',
                'dispatch_type': 'subagent',
            },
        )
        with caplog.at_level('ERROR'):
            parsed = plugin_jobs._load_template_file(tmp_path / 'rogue')
        assert parsed is None
        assert 'required `subagent:` key' in caplog.text

    def test_invalid_dispatch_type_rejected(self, tmp_path, caplog):
        _write_template(
            tmp_path / 'rogue',
            {
                'description': 'd',
                'system_prompt': 'sp',
                'notify': 'silent',
                'model': 'anthropic:claude-haiku-4-5-20251001',
                'dispatch_type': 'channel',  # not allowed
            },
        )
        with caplog.at_level('ERROR'):
            parsed = plugin_jobs._load_template_file(tmp_path / 'rogue')
        assert parsed is None
        assert 'invalid dispatch_type' in caplog.text

    def test_valid_tool_template_accepted(self, tmp_path):
        _write_template(
            tmp_path / 'sync',
            {
                'description': 'sync',
                'system_prompt': 'unused',
                'notify': 'silent',
                'model': 'anthropic:claude-haiku-4-5-20251001',
                'dispatch_type': 'tool',
                'tool': 'docker.list',
                'tool_params': {'format': 'json'},
            },
        )
        parsed = plugin_jobs._load_template_file(tmp_path / 'sync')
        assert parsed is not None
        assert parsed['dispatch_type'] == 'tool'
        assert parsed['tool'] == 'docker.list'


# ---------------------------------------------------------------------------
# NotifyPolicy keeps working regardless of dispatch type
# ---------------------------------------------------------------------------


def test_dispatch_type_is_orthogonal_to_notify_policy():
    """Sanity: adding dispatch_type did not break the notify-policy coupling."""
    job = _make_job(dispatch_type='tool', tool='demo.ping', notify=NotifyPolicy.SILENT)
    assert job.notify is NotifyPolicy.SILENT
    assert job.dispatch_type is JobDispatchType.TOOL
