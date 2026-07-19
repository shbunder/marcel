"""Subagents on the harness SubAgents capability (FEAT-260718-b6d1da).

Doc parsing (FR1 — every frontmatter field), the scoping chain, child
assembly (roles, tiers, recursion), and the acceptance scenarios as
scripted FunctionModel round-trips through a real admin build.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from marcel_core.capabilities.subagents import (
    SubagentNotFoundError,
    build_child_agent,
    build_subagents_capability,
    load_agent_doc,
    load_agent_docs,
)
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    """Agent() infers providers at construction — fake the env keys."""
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-openai-test-fake')


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Isolated zoo + data roots; returns (zoo, data)."""
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    data = tmp_path / 'data'
    (zoo / 'agents').mkdir(parents=True)
    data.mkdir()
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data))
    monkeypatch.setattr(_root, '_DATA_ROOT', data)
    return zoo, data


def _write(dirpath: Path, name: str, frontmatter: str, body: str = 'You are a child.') -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f'{name}.md').write_text(f'---\n{frontmatter}---\n\n{body}\n')


class TestDocParsing:
    def test_minimal_doc_defaults(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'mini', 'description: tiny\n')
        (doc,) = load_agent_docs()
        assert doc.name == 'mini'  # falls back to the file stem
        assert doc.model is None and doc.tools is None
        assert doc.max_requests is None and doc.timeout_seconds == 300

    def test_all_fields_parse(self, roots):
        zoo, _ = roots
        _write(
            zoo / 'agents',
            'full',
            'name: full\ndescription: d\nmodel: openai:gpt-4o\n'
            'tools: [marcel, web]\ndisallowed_tools: [web]\n'
            'max_requests: 9\ntimeout_seconds: 42\n',
        )
        (doc,) = load_agent_docs()
        assert doc.model == 'openai:gpt-4o'
        assert doc.tools == ['marcel', 'web'] and doc.disallowed_tools == ['web']
        assert doc.max_requests == 9 and doc.timeout_seconds == 42

    def test_inherit_maps_to_none(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'model: inherit\n')
        assert load_agent_docs()[0].model is None

    @pytest.mark.parametrize('tier', ['fast', 'standard', 'power'])
    def test_tier_names_become_sentinels(self, roots, tier):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', f'model: {tier}\n')
        assert load_agent_docs()[0].model == f'tier:{tier}'

    def test_removed_backup_tier_skipped(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'model: backup\n')
        assert load_agent_docs() == []

    def test_zero_timeout_survives_parsing(self, roots):
        """Regression: the old loader's `or`-chain swallowed a falsy 0 and
        silently defaulted to 300 — a doc saying 0 must mean 0."""
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'timeout_seconds: 0\n')
        assert load_agent_docs()[0].timeout_seconds == 0

    def test_camelcase_aliases(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'maxRequests: 5\ntimeoutSeconds: 7\ndisallowedTools: [web]\n')
        (doc,) = load_agent_docs()
        assert doc.max_requests == 5 and doc.timeout_seconds == 7
        assert doc.disallowed_tools == ['web']

    def test_unknown_keys_tolerated(self, roots):
        """FR1: forward-compat with other runtimes' agent files."""
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'color: cyan\nproactive: true\n')
        (doc,) = load_agent_docs()
        assert doc.name == 'a'

    def test_hidden_and_underscore_files_ignored(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', '_draft', 'description: x\n')
        _write(zoo / 'agents', '.hidden', 'description: x\n')
        _write(zoo / 'agents', 'real', 'description: x\n')
        assert [d.name for d in load_agent_docs()] == ['real']

    def test_unknown_name_raises_with_available(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'real', 'description: x\n')
        with pytest.raises(SubagentNotFoundError, match="ghost.*'real'"):
            load_agent_doc('ghost')


class TestScopingChain:
    def test_most_specific_wins(self, roots):
        zoo, data = roots
        _write(zoo / 'agents', 'a', 'description: zoo-global\n')
        _write(data / 'agents', 'a', 'description: data-global\n')
        _write(zoo / 'users' / 'shaun' / 'agents', 'a', 'description: zoo-user\n')
        _write(data / 'users' / 'shaun' / 'agents', 'a', 'description: data-user\n')

        assert load_agent_docs()[0].description == 'data-global'  # no user chain
        assert load_agent_docs('shaun')[0].description == 'data-user'

    def test_invalid_slug_falls_back_to_global(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'description: global\n')
        docs = load_agent_docs('../etc')
        assert [d.description for d in docs] == ['global']


class TestChildAssembly:
    def test_tier_sentinel_resolves_through_chain(self, roots, monkeypatch):
        """Acceptance: a tier-sentinel model resolves via the model chain."""
        from marcel_core.config import settings

        zoo, _ = roots
        _write(zoo / 'agents', 'heavy', 'model: power\n')
        monkeypatch.setattr(settings, 'marcel_power_model', 'openai:gpt-4o')
        child = build_child_agent(load_agent_doc('heavy'), role='admin')
        assert child.model is not None
        assert getattr(child.model, 'model_name', child.model) == 'gpt-4o'

    def test_unconfigured_tier_agent_not_offered(self, roots, monkeypatch):
        from marcel_core.config import settings

        zoo, _ = roots
        _write(zoo / 'agents', 'heavy', 'model: power\n')
        _write(zoo / 'agents', 'lite', 'model: inherit\n')
        monkeypatch.setattr(settings, 'marcel_power_model', '')
        cap = build_subagents_capability(user_slug='shaun', role='admin')
        assert cap is not None
        assert [w.name for w in cap.agents] == ['lite']

    def test_inherit_builds_model_less(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'model: inherit\n')
        child = build_child_agent(load_agent_doc('a'), role='admin')
        assert child.model is None

    def test_non_admin_children_carry_no_admin_tools(self, roots):
        """Acceptance: role rules hold for children."""
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'description: x\n')  # no tools: → role pool
        from marcel_core.capabilities.subagents import _tool_filter
        from marcel_core.harness.agent import admin_tool_names

        cap = build_subagents_capability(user_slug='kid', role='user')
        assert cap is not None
        # Structural check via a scripted run is in TestDelegationScenarios;
        # here: the doc's resolved filter never includes admin tools.
        assert not (_tool_filter(load_agent_doc('a'), 'user') & admin_tool_names())

    def test_max_requests_becomes_isolated_usage_limits(self, roots):
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'max_requests: 7\n')
        cap = build_subagents_capability(user_slug='shaun', role='admin')
        assert cap is not None
        (wrapper,) = cap.agents
        assert wrapper.usage_limits is not None and wrapper.usage_limits.request_limit == 7
        assert wrapper.timeout_seconds == 300.0

    def test_children_run_under_marcel_policy(self, roots):
        """Acceptance: child tool calls stay policy-gated — every child is a
        full create_marcel_agent build carrying MarcelPolicy."""
        zoo, _ = roots
        _write(zoo / 'agents', 'a', 'description: x\n')
        cap = build_subagents_capability(user_slug='shaun', role='admin')
        assert cap is not None
        child = cap.agents[0].agent.wrapped  # type: ignore[attr-defined]  # FreshTurnAgent → real Agent
        cap_names = {type(c).__name__ for c in child.root_capability.capabilities}
        assert 'MarcelPolicy' in cap_names

    def test_no_docs_means_no_capability(self, roots):
        assert build_subagents_capability(user_slug='shaun', role='admin') is None


class TestFreshTurnWrapper:
    @pytest.mark.asyncio
    async def test_deps_derived_per_delegation(self, roots):
        from marcel_core.capabilities.subagents import _fresh_turn_wrapper
        from marcel_core.harness.context import MarcelDeps

        seen: dict = {}

        class _Stub:
            model = None
            name = 'stub'

            async def run(self, *args, deps=None, **kwargs):
                seen['deps'] = deps
                return 'ok'

        wrapper = _fresh_turn_wrapper(_Stub(), 'explore')
        parent = MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli', role='admin')
        parent.turn.notified = True
        await wrapper.run('task', deps=parent)

        child_deps = seen['deps']
        assert child_deps.conversation_id == 'shaun:cli:delegate:explore'
        assert child_deps.turn is not parent.turn
        assert child_deps.turn.notified is False  # fresh TurnState, no leakage


# ---------------------------------------------------------------------------
# Scripted acceptance scenarios — real admin build, FunctionModel parent+child
# ---------------------------------------------------------------------------


def _delegating_handler(reqs: list, child_marker: str, child_response):
    """A FunctionModel handler: parent delegates once, child answers."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    def handler(messages, info):
        reqs.append((messages, info))
        if child_marker in str(messages):
            return child_response(messages, info)
        if len([r for r in reqs if child_marker not in str(r[0])]) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name='delegate', args={'agent_name': 'explore', 'task': 'find it'})]
            )
        return ModelResponse(parts=[TextPart('parent-done')])

    return handler


def _admin_run(handler):
    from pydantic_ai.models.function import FunctionModel

    from marcel_core.harness.agent import create_marcel_agent
    from marcel_core.harness.context import MarcelDeps

    agent = create_marcel_agent(FunctionModel(handler), system_prompt='PARENT_SYS', role='admin', user_slug='shaun')
    deps = MarcelDeps(user_slug='shaun', conversation_id='shaun:cli', channel='cli', role='admin')
    return agent.run('go', deps=deps)


class TestDelegationScenarios:
    @pytest.mark.asyncio
    async def test_round_trip_with_fresh_history(self, roots):
        """Acceptance scenario 1: the child runs with its own fresh history and
        its scripted output returns to the parent as the tool result."""
        from pydantic_ai.messages import ModelResponse, TextPart

        zoo, _ = roots
        _write(zoo / 'agents', 'explore', 'model: inherit\ntools: [marcel]\n', body='You are EXPLORE_CHILD.')

        reqs: list = []
        handler = _delegating_handler(
            reqs, 'EXPLORE_CHILD', lambda m, i: ModelResponse(parts=[TextPart('child-answer-42')])
        )
        result = await _admin_run(handler)

        assert result.output == 'parent-done'
        child_reqs = [r for r in reqs if 'EXPLORE_CHILD' in str(r[0])]
        assert len(child_reqs) == 1
        assert 'PARENT_SYS' not in str(child_reqs[0][0])  # fresh history
        assert any('child-answer-42' in str(r[0]) for r in reqs if 'EXPLORE_CHILD' not in str(r[0]))

    @pytest.mark.asyncio
    async def test_recursion_blocked_in_children(self, roots):
        """Acceptance scenario 2: the delegate tool is not among the child's tools."""
        from pydantic_ai.messages import ModelResponse, TextPart

        zoo, _ = roots
        # No tools: key — role-default pool, where the guard must also hold.
        _write(zoo / 'agents', 'explore', 'model: inherit\n', body='You are EXPLORE_CHILD.')

        reqs: list = []
        handler = _delegating_handler(reqs, 'EXPLORE_CHILD', lambda m, i: ModelResponse(parts=[TextPart('done')]))
        await _admin_run(handler)

        child_reqs = [r for r in reqs if 'EXPLORE_CHILD' in str(r[0])]
        parent_reqs = [r for r in reqs if 'EXPLORE_CHILD' not in str(r[0])]
        assert 'delegate' in {t.name for t in parent_reqs[0][1].function_tools}
        assert 'delegate' not in {t.name for t in child_reqs[0][1].function_tools}

    @pytest.mark.asyncio
    async def test_non_admin_parent_has_no_delegate(self, roots):
        """Role rules: the capability is admin-tier — a user build never sees it."""
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel

        from marcel_core.harness.agent import create_marcel_agent
        from marcel_core.harness.context import MarcelDeps

        zoo, _ = roots
        _write(zoo / 'agents', 'explore', 'model: inherit\n')

        reqs: list = []

        def handler(messages, info):
            reqs.append((messages, info))
            return ModelResponse(parts=[TextPart('hi')])

        agent = create_marcel_agent(FunctionModel(handler), system_prompt='u', role='user', user_slug='kid')
        deps = MarcelDeps(user_slug='kid', conversation_id='kid:cli', channel='cli')
        await agent.run('go', deps=deps)
        assert 'delegate' not in {t.name for t in reqs[0][1].function_tools}

    @pytest.mark.asyncio
    async def test_timeout_returns_readable_string(self, roots):
        """Acceptance scenario 5: a stalling child yields a readable soft
        result for the parent, not an exception."""
        from pydantic_ai.messages import ModelResponse, TextPart

        zoo, _ = roots
        _write(zoo / 'agents', 'explore', 'model: inherit\ntimeout_seconds: 0\n', body='You are EXPLORE_CHILD.')

        async def stalling_child(m, i):
            await asyncio.sleep(0.5)
            return ModelResponse(parts=[TextPart('never')])

        sync_reqs: list = []

        async def handler(messages, info):
            sync_reqs.append((messages, info))
            if 'EXPLORE_CHILD' in str(messages):
                return await stalling_child(messages, info)
            if len(sync_reqs) == 1:
                from pydantic_ai.messages import ToolCallPart

                return ModelResponse(
                    parts=[ToolCallPart(tool_name='delegate', args={'agent_name': 'explore', 'task': 'go'})]
                )
            return ModelResponse(parts=[TextPart('parent-recovered')])

        result = await _admin_run(handler)
        assert result.output == 'parent-recovered'
        parent_followups = [r for r in sync_reqs if 'time budget' in str(r[0])]
        assert parent_followups, 'parent never saw the readable timeout result'
