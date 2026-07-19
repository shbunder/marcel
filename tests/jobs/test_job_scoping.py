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
            'from fastmcp import FastMCP\n\nmcp = FastMCP("srv")\n\n'
            '@mcp.tool\ndef headlines() -> str:\n    """Current headlines."""\n    return "none"\n'
        )

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
