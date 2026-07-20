"""web as a capability (FEAT-260720-631cb3, ADR-260720-9318b1)."""

from __future__ import annotations

import pytest

from marcel_core.composition import CODE_MODE_ELIGIBLE, build_capabilities


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')
    from marcel_core.storage import _root

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


def _ids(**kwargs) -> set:
    return {getattr(c, 'id', None) for c in build_capabilities(**kwargs)}


class TestComposition:
    def test_attached_by_default_and_on_filter(self):
        assert 'web-tools' in _ids(role='user')
        assert 'web-tools' in _ids(role='admin', tool_filter={'web'})

    def test_absent_on_empty_filter_and_non_web_filter(self):
        assert 'web-tools' not in _ids(role='admin', tool_filter=set())
        assert 'web-tools' not in _ids(role='user', tool_filter={'marcel'})

    def test_web_is_not_a_registry_entry(self):
        from marcel_core.harness.agent import _TOOL_REGISTRY

        assert 'web' not in {name for name, _fn, _r in _TOOL_REGISTRY}

    def test_web_stays_code_mode_eligible(self):
        assert 'web' in CODE_MODE_ELIGIBLE


class TestCodeModeStillWrapsWeb:
    @pytest.mark.asyncio
    async def test_run_code_sees_web_as_sandboxed_not_native(self):
        """The load-bearing guarantee: a non-deferred web capability is still
        folded into run_code (CodeMode keeps deferred tools native — web must
        not become deferred). We assert web is NOT offered as a bare native
        tool alongside run_code on the request."""
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel

        from marcel_core.harness.agent import create_marcel_agent
        from marcel_core.harness.context import MarcelDeps

        seen: list = []

        def handler(messages, info):
            seen.append({t.name for t in info.function_tools})
            return ModelResponse(parts=[TextPart('ok')])

        agent = create_marcel_agent(
            FunctionModel(handler),
            system_prompt='x',
            role='user',
            user_slug='shaun',
            code_mode=True,
        )
        await agent.run('hi', deps=MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli'))
        tools = seen[0]
        assert 'run_code' in tools  # CodeMode active
        assert 'web' not in tools  # folded into the sandbox, not a bare native tool


class TestNoIdCollisionWithWebSkill:
    @pytest.mark.asyncio
    async def test_web_skill_and_web_capability_coexist(self, tmp_path, monkeypatch):
        from pydantic_ai.models.test import TestModel

        from marcel_core.config import settings
        from marcel_core.harness.agent import create_marcel_agent
        from marcel_core.harness.context import MarcelDeps

        zoo = tmp_path / 'zoo'
        skill = zoo / 'skills' / 'web'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: web\ndescription: Web research.\n---\n\nUse the web tool.\n')
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        agent = create_marcel_agent(TestModel(call_tools=[]), system_prompt='x', role='user', user_slug='shaun')
        ids = {getattr(c, 'id', None) for c in agent.root_capability.capabilities}
        assert 'web-tools' in ids  # the tool capability
        assert 'web' in ids  # the skill capability — distinct id, no crash
        result = await agent.run('hi', deps=MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli'))
        assert result.output
