"""Turn-quality capabilities (FEAT-260718-637764): Planning per tier + LimitWarner.

Scripted FunctionModel turns through real builds — the acceptance scenarios
from the requirements page.
"""

from __future__ import annotations

import pytest

from marcel_core.composition import TURN_REQUEST_LIMIT, build_capabilities
from marcel_core.harness.model_chain import Tier


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')


def _cap_names(**kwargs) -> set[str]:
    return {type(c).__name__ for c in build_capabilities(**kwargs)}


class TestTierConditionalAssembly:
    def test_standard_and_power_get_planning(self):
        assert 'Planning' in _cap_names(role='user', tier=Tier.STANDARD)
        assert 'Planning' in _cap_names(role='user', tier=Tier.POWER)

    def test_local_and_fast_skip_planning(self):
        assert 'Planning' not in _cap_names(role='user', tier=Tier.LOCAL)
        assert 'Planning' not in _cap_names(role='user', tier=Tier.FAST)

    def test_every_tier_gets_limit_warner(self):
        for tier in Tier:
            assert 'LimitWarner' in _cap_names(role='user', tier=tier)

    def test_lean_paths_skip_both(self):
        """Jobs, subagent children and the explain tier pass no tier —
        NFR1: nothing regresses the paths that opted out."""
        names = _cap_names(role='user')
        assert 'Planning' not in names and 'LimitWarner' not in names

    @staticmethod
    def _limit_warner(tier: Tier):
        from pydantic_ai_harness.compaction import LimitWarner

        (warner,) = [c for c in build_capabilities(role='user', tier=tier) if isinstance(c, LimitWarner)]
        return warner

    def test_local_tier_gets_the_small_context_budget(self):
        from marcel_core.config import settings

        assert self._limit_warner(Tier.LOCAL).max_context_tokens == settings.marcel_limit_warn_context_tokens_local
        assert self._limit_warner(Tier.STANDARD).max_context_tokens == settings.marcel_limit_warn_context_tokens

    def test_iteration_cap_matches_the_runners_usage_limit(self):
        assert self._limit_warner(Tier.STANDARD).max_iterations == TURN_REQUEST_LIMIT


# ---------------------------------------------------------------------------
# Scripted scenarios
# ---------------------------------------------------------------------------


def _make_agent(handler, *, tier: Tier):
    from pydantic_ai.models.function import FunctionModel

    from marcel_core.harness.agent import create_marcel_agent

    return create_marcel_agent(
        FunctionModel(handler),
        system_prompt='PARENT',
        role='user',
        tier=tier,
        memory=False,
        code_mode=False,
        skills=False,
        connectors=False,
    )


def _deps():
    from marcel_core.harness.context import MarcelDeps

    return MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli')


class TestPlanningScenarios:
    @pytest.mark.asyncio
    async def test_model_plans_and_plan_state_recoverable(self):
        """Acceptance scenario 1: write_plan succeeds, the reminder rides the
        next request, and the final plan state is recoverable from history
        (the tool return carries the rendered checklist)."""
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

        reqs: list = []

        def handler(messages, info):
            reqs.append((messages, info))
            if len(reqs) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name='write_plan',
                            args={
                                'items': [
                                    {'content': 'inspect the fridge', 'status': 'in_progress'},
                                    {'content': 'draft the grocery list'},
                                ]
                            },
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart('planned and done')])

        agent = _make_agent(handler, tier=Tier.POWER)
        result = await agent.run('plan dinner', deps=_deps())

        assert result.output == 'planned and done'
        assert 'write_plan' in {t.name for t in reqs[0][1].function_tools}
        # The reminder rides the follow-up request, outside the cached prefix.
        followup = str(reqs[1][0])
        assert '<plan-reminder>' in followup
        assert 'inspect the fridge' in followup
        # Recoverable from history: the tool return carries the checklist.
        history = str(result.all_messages())
        assert 'Plan updated: 2 step(s)' in history

    @pytest.mark.asyncio
    async def test_write_plan_absent_on_local_tier(self):
        """Acceptance scenario 2: planning is absent on the local tier."""
        from pydantic_ai.messages import ModelResponse, TextPart

        reqs: list = []

        def handler(messages, info):
            reqs.append((messages, info))
            return ModelResponse(parts=[TextPart('ok')])

        agent = _make_agent(handler, tier=Tier.LOCAL)
        await agent.run('hi', deps=_deps())
        assert 'write_plan' not in {t.name for t in reqs[0][1].function_tools}


class TestLimitWarnerScenarios:
    @pytest.mark.asyncio
    async def test_warning_appears_past_threshold(self, monkeypatch):
        """Acceptance scenario 3: past the threshold the request carries the
        [LimitWarner] warning; below it, no warning is injected."""
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )

        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_limit_warn_context_tokens', 100)

        reqs: list = []

        def handler(messages, info):
            reqs.append(str(messages))
            return ModelResponse(parts=[TextPart('ok')])

        # ~400 chars ≈ 100 estimated tokens — past 0.7 * 100.
        fat_history = [
            ModelRequest(parts=[UserPromptPart(content='x' * 400)]),
            ModelResponse(parts=[TextPart('noted')]),
        ]
        agent = _make_agent(handler, tier=Tier.STANDARD)
        await agent.run('hi', deps=_deps(), message_history=fat_history)
        assert '[LimitWarner]' in reqs[-1]

        reqs.clear()
        agent = _make_agent(handler, tier=Tier.STANDARD)
        await agent.run('hi', deps=_deps())
        assert '[LimitWarner]' not in reqs[-1]
