"""Job scoping (FEAT-260718-49a01a): model fields + name resolution.

The ``connectors:`` declaration and the resolver both fail loud with
candidates listed — a typo'd digest job should die in conversation at save
time, not silently run without its tools at 07:00.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from marcel_core.jobs.models import JobDefinition, TriggerSpec, TriggerType
from marcel_core.jobs.scoping import JobScopingError, resolve_job_scoping
from marcel_core.storage import _root


def _agent_job(**overrides: Any) -> JobDefinition:
    base: dict[str, Any] = {
        'name': 'Digest',
        'trigger': TriggerSpec(type=TriggerType.CRON, cron='0 7 * * *'),
        'system_prompt': 'You are a digest writer.',
        'task': 'Write the digest.',
    }
    base.update(overrides)
    return JobDefinition(**base)


@pytest.fixture
def scoping_zoo(tmp_path, monkeypatch):
    """A zoo with one paired skill+connector and one admin-scoped connector."""
    from marcel_core.config import settings

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path / 'data')
    zoo = tmp_path / 'zoo'

    skill = zoo / 'skills' / 'news'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(
        '---\nname: news\ndescription: Read the news digest.\n'
        'metadata:\n  marcel-connectors: news\n---\n\n'
        'Call `headlines()` for the current digest.\n'
    )

    for name, scope in (('news', 'all'), ('dockerd', 'admin')):
        park = zoo / 'connectors' / name
        park.mkdir(parents=True)
        (park / 'connector.yaml').write_text(
            f'name: {name}\ndescription: {name} tools\n'
            'server: {transport: inprocess, module: server.py}\n'
            'auth: {mode: none, per_user: false}\n'
            f'scope: {scope}\n'
        )
        (park / 'server.py').write_text(
            'from fastmcp import FastMCP\n\n'
            'def build(user_slug):\n'
            '    mcp = FastMCP("srv")\n\n'
            '    @mcp.tool\n'
            '    def headlines() -> str:\n'
            '        """Current headlines."""\n'
            "        return f'serving:{user_slug}'\n\n"
            '    return mcp\n'
        )

    # Fresh per-test connector registry: the process-wide one keys instances by
    # (connector name, slug), which would hand a later test a server built from
    # an earlier test's tmp zoo.
    import marcel_core.connectors.toolset as toolset_mod

    monkeypatch.setattr(toolset_mod, '_REGISTRY', None)

    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    return zoo


class TestConnectorsField:
    def test_round_trips_through_job_file(self, tmp_path, monkeypatch):
        from marcel_core.jobs import load_job, save_job

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        job = _agent_job(users=['shaun'], skills=['news'], connectors=['news'])
        save_job(job)
        loaded = load_job(job.id)
        assert loaded is not None
        assert loaded.skills == ['news']
        assert loaded.connectors == ['news']

    def test_tool_dispatch_forbids_connectors(self):
        with pytest.raises(ValidationError, match='cannot carry `connectors`'):
            _agent_job(dispatch_type='tool', tool='news.sync', connectors=['news'])

    def test_subagent_dispatch_forbids_connectors(self):
        with pytest.raises(ValidationError, match='cannot carry `connectors`'):
            _agent_job(dispatch_type='subagent', subagent='digester', connectors=['news'])


class TestResolveJobScoping:
    def test_resolves_declared_names(self, scoping_zoo):
        scope = resolve_job_scoping('shaun', ['news'], ['news'])
        assert [d.name for d in scope.skill_docs] == ['news']
        assert [d.config.name for d in scope.connector_docs] == ['news']

    def test_skill_connectors_join_implicitly(self, scoping_zoo):
        scope = resolve_job_scoping('shaun', ['news'], [])
        assert [d.config.name for d in scope.connector_docs] == ['news']

    def test_unknown_skill_lists_candidates(self, scoping_zoo):
        with pytest.raises(JobScopingError, match=r'skill\(s\).*newz.*Available skills: news'):
            resolve_job_scoping('shaun', ['newz'], [])

    def test_unknown_connector_lists_candidates(self, scoping_zoo):
        with pytest.raises(JobScopingError, match=r'connector\(s\).*banking.*Available connectors: news'):
            resolve_job_scoping('shaun', [], ['banking'])

    def test_admin_scoped_connector_hidden_from_user_role(self, scoping_zoo):
        # 'shaun' has no profile in this tmp data root, so get_user_role
        # falls back to 'user' — dockerd (scope: admin) must not resolve.
        with pytest.raises(JobScopingError, match='dockerd'):
            resolve_job_scoping('shaun', [], ['dockerd'])

    def test_admin_scoped_connector_resolves_for_admin_user(self, scoping_zoo, monkeypatch):
        from marcel_core.storage import users as users_mod

        monkeypatch.setattr(users_mod, 'get_user_role', lambda slug: 'admin')
        scope = resolve_job_scoping('shaun', [], ['dockerd'])
        assert [d.config.name for d in scope.connector_docs] == ['dockerd']

    def test_system_user_resolves_at_base_role(self, scoping_zoo):
        from marcel_core.jobs import SYSTEM_USER

        scope = resolve_job_scoping(SYSTEM_USER, [], ['news'])
        assert [d.config.name for d in scope.connector_docs] == ['news']
        with pytest.raises(JobScopingError):
            resolve_job_scoping(SYSTEM_USER, [], ['dockerd'])

    def test_empty_declaration_resolves_empty(self, scoping_zoo):
        scope = resolve_job_scoping('shaun', [], [])
        assert scope.skill_docs == [] and scope.connector_docs == []


# ---------------------------------------------------------------------------
# Scoped executor assembly (STORY-260719-12b163) — scripted end-to-end runs
# ---------------------------------------------------------------------------


def _scripted_run(monkeypatch, script):
    """Route execute_job's agent build through a FunctionModel.

    ``script`` maps call index -> ModelResponse; every request's (messages,
    info) pair is captured. Returns (captured, create_kwargs).
    """
    from pydantic_ai.messages import ModelResponse, TextPart

    import marcel_core.harness.agent as agent_mod

    captured: list = []
    create_kwargs: list = []
    # Bind the genuine factory, not a wrapper left by an earlier _scripted_run
    # in the same test (wrapping a wrapper would silently discard this run's
    # FunctionModel and route captures to the previous list).
    real_create = getattr(agent_mod.create_marcel_agent, '__wrapped__', agent_mod.create_marcel_agent)

    def handler(messages, info):
        captured.append((messages, info))
        idx = len(captured) - 1
        if idx in script:
            return script[idx]
        return ModelResponse(parts=[TextPart('done')])

    from pydantic_ai.models.function import FunctionModel

    def wrapped(model=None, **kwargs):
        create_kwargs.append(kwargs)
        return real_create(FunctionModel(handler), **kwargs)

    wrapped.__wrapped__ = real_create  # type: ignore[attr-defined]
    monkeypatch.setattr(agent_mod, 'create_marcel_agent', wrapped)
    return captured, create_kwargs


def _tool_names(info) -> set[str]:
    return {t.name for t in info.function_tools}


def _request_size(messages, info) -> int:
    return len(str(messages)) + sum(len(str(t)) for t in info.function_tools)


class TestScopedExecution:
    @pytest.mark.asyncio
    async def test_scoped_digest_runs_lean(self, scoping_zoo, monkeypatch):
        """Acceptance scenario 1: the request carries the news skill body and
        only the declared surface — no memory block, no catalog, no unrelated
        tools."""
        from marcel_core.jobs.executor import SCOPED_JOB_TOOL_NAMES, execute_job

        captured, create_kwargs = _scripted_run(monkeypatch, {})
        job = _agent_job(users=['shaun'], skills=['news'])
        run = await execute_job(job, 'test')

        assert run.status.value == 'completed', run.error
        assert create_kwargs[0]['tool_filter'] == set(SCOPED_JOB_TOOL_NAMES)
        assert create_kwargs[0]['memory'] is False
        assert len(create_kwargs[0]['extra_capabilities']) == 2  # eager skill + news MCP

        messages, info = captured[0]
        names = _tool_names(info)
        assert 'headlines' in names  # the declared connector, non-deferred
        assert 'read_skill_resource' in names  # the eager skill's resource tool
        assert 'marcel' in names
        # Nothing else: no web, no charts, no job management, no catalog.
        assert not names & {'web', 'generate_chart', 'create_job', 'load_capability'}
        text = str(messages)
        assert 'Call `headlines()` for the current digest.' in text  # skill body eager
        assert 'User preferences' not in text  # no memory injection

    @pytest.mark.asyncio
    async def test_undeclared_connector_tool_is_unreachable(self, scoping_zoo, monkeypatch):
        """Acceptance scenario 2: a tool from an undeclared connector was never
        in the toolset — calling it yields the framework's unknown-tool retry."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        from marcel_core.jobs.executor import execute_job

        captured, _ = _scripted_run(
            monkeypatch,
            {0: ModelResponse(parts=[ToolCallPart(tool_name='transactions', args={})])},
        )
        job = _agent_job(users=['shaun'], skills=['news'])
        run = await execute_job(job, 'test')

        assert run.status.value == 'completed'
        assert all('transactions' not in _tool_names(info) for _messages, info in captured)
        assert 'transactions' in str(captured[1][0]).lower()  # the retry names the unknown tool

    @pytest.mark.asyncio
    async def test_connector_serves_the_job_users_identity(self, scoping_zoo, monkeypatch):
        """Acceptance scenario 4: the registry keys instances per (connector,
        user), so the park serves the job's user — same delivery as a
        conversation turn."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        from marcel_core.jobs.executor import execute_job

        captured, _ = _scripted_run(
            monkeypatch,
            {0: ModelResponse(parts=[ToolCallPart(tool_name='headlines', args={})])},
        )
        job = _agent_job(users=['shaun'], connectors=['news'])
        run = await execute_job(job, 'test')

        assert run.status.value == 'completed', run.error
        assert 'serving:shaun' in str(captured[1][0])

    @pytest.mark.asyncio
    async def test_scoping_failure_fails_run_as_config(self, scoping_zoo, monkeypatch):
        """A name that no longer resolves at run time fails loud — never a
        silent run without the declared tools."""
        from marcel_core.jobs.executor import execute_job

        _scripted_run(monkeypatch, {})
        job = _agent_job(users=['shaun'], connectors=['banking'])
        run = await execute_job(job, 'test')

        assert run.status.value == 'failed'
        assert run.error_category == 'config'
        assert run.error is not None and 'banking' in run.error and 'news' in run.error

    @pytest.mark.asyncio
    async def test_scoped_request_materially_smaller_than_unscoped(self, scoping_zoo, monkeypatch):
        """NFR1: same job, scoped vs unscoped — the scoped request must be
        materially smaller (guard against silent regression to full assembly)."""
        from marcel_core.jobs.executor import execute_job

        captured_scoped, _ = _scripted_run(monkeypatch, {})
        scoped_job = _agent_job(users=['shaun'], skills=['news'])
        run = await execute_job(scoped_job, 'test')
        assert run.status.value == 'completed', run.error
        scoped_size = _request_size(*captured_scoped[0])

        captured_legacy, _ = _scripted_run(monkeypatch, {})
        legacy_job = _agent_job(users=['shaun'])
        run = await execute_job(legacy_job, 'test')
        assert run.status.value == 'completed', run.error
        legacy_size = _request_size(*captured_legacy[0])

        assert len(_tool_names(captured_scoped[0][1])) < len(_tool_names(captured_legacy[0][1]))
        assert scoped_size < legacy_size * 0.8, (scoped_size, legacy_size)


# ---------------------------------------------------------------------------
# Save-time validation through the job tools (STORY-260719-bde17a)
# ---------------------------------------------------------------------------


def _ctx(user_slug: str = 'shaun'):
    from unittest.mock import MagicMock

    from marcel_core.harness.context import MarcelDeps

    ctx = MagicMock()
    ctx.deps = MarcelDeps(user_slug=user_slug, conversation_id='conv-1', channel='cli')
    return ctx


class TestSaveTimeValidation:
    @pytest.mark.asyncio
    async def test_create_job_rejects_unknown_connector_with_candidates(self, scoping_zoo):
        from marcel_core.jobs.tool import create_job

        result = await create_job(
            _ctx(),
            name='Digest',
            task='Write it.',
            trigger_type='cron',
            system_prompt='You write digests.',
            cron='0 7 * * *',
            connectors=['banking'],
        )
        assert 'Cannot save job' in result
        assert 'banking' in result and 'news' in result

    @pytest.mark.asyncio
    async def test_create_job_accepts_valid_scoping(self, scoping_zoo):
        from marcel_core.jobs import load_job
        from marcel_core.jobs.tool import create_job

        result = await create_job(
            _ctx(),
            name='Digest',
            task='Write it.',
            trigger_type='cron',
            system_prompt='You write digests.',
            cron='0 7 * * *',
            skills=['news'],
        )
        assert 'Job created' in result
        import re

        match = re.search(r'ID: `(\w+)`', result)
        assert match is not None
        job = load_job(match.group(1))
        assert job is not None and job.skills == ['news']

    @pytest.mark.asyncio
    async def test_update_job_validates_and_replaces_scoping(self, scoping_zoo):
        from marcel_core.jobs import save_job
        from marcel_core.jobs.tool import update_job

        job = _agent_job(users=['shaun'])
        save_job(job)

        result = await update_job(_ctx(), job.id, connectors=['nope'])
        assert 'Cannot save job' in result and 'nope' in result

        result = await update_job(_ctx(), job.id, skills=['news'])
        assert 'updated' in result
        from marcel_core.jobs import load_job

        loaded = load_job(job.id)
        assert loaded is not None and loaded.skills == ['news']
