"""Channel guidance as a capability (FEAT-260720-089958)."""

from __future__ import annotations

import pytest

from marcel_core.composition import build_capabilities
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


class TestComposition:
    def test_channel_capability_attached_when_channel_named(self):
        caps = {getattr(c, 'id', None) for c in build_capabilities(role='user', channel='cli')}
        assert 'channel:cli' in caps

    def test_lean_paths_skip_it(self):
        """Jobs, subagent children and the explain tier pass no channel."""
        caps = {getattr(c, 'id', None) for c in build_capabilities(role='user')}
        assert not any(str(c).startswith('channel:') for c in caps if c)


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_request_carries_the_channel_block(self):
        """The guidance text reaches the model request unchanged — the same
        block the prompt builder used to emit, now via the capability."""
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel

        from marcel_core.harness.agent import create_marcel_agent
        from marcel_core.harness.context import MarcelDeps

        reqs: list = []

        def handler(messages, info):
            reqs.append(str(messages))
            return ModelResponse(parts=[TextPart('ok')])

        agent = create_marcel_agent(
            FunctionModel(handler),
            system_prompt='# Marcel — who you are',
            role='user',
            channel='cli',
            memory=False,
            code_mode=False,
            skills=False,
            connectors=False,
        )
        deps = MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli')
        await agent.run('hi', deps=deps)

        assert '# Cli — how to respond' in reqs[0]
        # Identity block still precedes channel guidance, as before.
        assert reqs[0].index('# Marcel — who you are') < reqs[0].index('# Cli — how to respond')
