"""Tests for tools/delegate.py — the subagent delegation tool (ISSUE-074)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from marcel_core.config import settings
from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.delegate import delegate


@dataclass
class _FakeRunResult:
    output: str


class _FakeAgent:
    """Stand-in for a pydantic-ai Agent that records the run call."""

    def __init__(self, output: str = 'fake result'):
        self.output = output
        self.run_calls: list[dict[str, Any]] = []

    async def run(self, prompt: str, deps: Any = None, usage_limits: Any = None) -> _FakeRunResult:
        self.run_calls.append({'prompt': prompt, 'deps': deps, 'usage_limits': usage_limits})
        return _FakeRunResult(output=self.output)


@pytest.fixture
def agents_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
    agents_dir = tmp_path / 'agents'
    agents_dir.mkdir()
    return agents_dir


def _write_agent(agents_root: Path, name: str, frontmatter: str, body: str = 'You are a test subagent.') -> None:
    (agents_root / f'{name}.md').write_text(f'---\n{frontmatter}\n---\n\n{body}\n', encoding='utf-8')


def _ctx(role: str = 'admin', model: str | None = 'anthropic:claude-sonnet-4-6') -> MagicMock:
    deps = MarcelDeps(
        user_slug='shaun',
        conversation_id='conv-1',
        channel='cli',
        role=role,
        model=model,
    )
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


@pytest.fixture
def fake_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch ``create_marcel_agent`` so delegate calls return a captureable stub.

    Yields ``(calls, fake_agent_ref)`` where ``calls`` accumulates every
    kwarg dict the factory was invoked with, and ``fake_agent_ref['current']``
    is the most recently returned :class:`_FakeAgent`.
    """
    calls: list[dict[str, Any]] = []
    fake_agent_ref: dict[str, _FakeAgent] = {}

    def fake_create(**kwargs: Any) -> _FakeAgent:
        calls.append(kwargs)
        agent = _FakeAgent(output='fake result')
        fake_agent_ref['current'] = agent
        return agent

    monkeypatch.setattr('marcel_core.harness.agent.create_marcel_agent', fake_create)
    return calls, fake_agent_ref


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_unknown_agent_returns_error_string(self, agents_root: Path, fake_factory):
        result = await delegate(_ctx(), subagent_type='ghost', prompt='do stuff')
        assert 'delegate error' in result
        assert 'ghost' in result

    @pytest.mark.asyncio
    async def test_unknown_agent_does_not_build_subagent(self, agents_root: Path, fake_factory):
        calls, _ = fake_factory
        await delegate(_ctx(), subagent_type='ghost', prompt='do stuff')
        assert calls == []

    @pytest.mark.asyncio
    async def test_timeout_returns_error_string(self, agents_root: Path, monkeypatch: pytest.MonkeyPatch):
        _write_agent(agents_root, 'slowpoke', 'name: slowpoke\ndescription: d\ntimeout_seconds: 1')

        class _SlowAgent:
            async def run(self, *args, **kwargs):
                import asyncio

                await asyncio.sleep(5)
                return _FakeRunResult('never returned')

        monkeypatch.setattr('marcel_core.harness.agent.create_marcel_agent', lambda **kw: _SlowAgent())
        result = await delegate(_ctx(), subagent_type='slowpoke', prompt='take your time')
        assert 'delegate error' in result
        assert 'timed out' in result

    @pytest.mark.asyncio
    async def test_subagent_exception_is_captured(self, agents_root: Path, monkeypatch: pytest.MonkeyPatch):
        _write_agent(agents_root, 'broken', 'name: broken\ndescription: d')

        class _BrokenAgent:
            async def run(self, *args, **kwargs):
                raise RuntimeError('kaboom')

        monkeypatch.setattr('marcel_core.harness.agent.create_marcel_agent', lambda **kw: _BrokenAgent())
        result = await delegate(_ctx(), subagent_type='broken', prompt='explode')
        assert 'delegate error' in result
        assert 'kaboom' in result


# ---------------------------------------------------------------------------
# Tool filter resolution
# ---------------------------------------------------------------------------


class TestToolFilterResolution:
    @pytest.mark.asyncio
    async def test_explicit_allowlist_drops_delegate_by_default(self, agents_root: Path, fake_factory):
        """An agent with ``tools: [read_file, web]`` must not inherit ``delegate``.

        The recursion guard: subagents cannot spawn further subagents unless
        they explicitly opt in by listing ``delegate`` in their allowlist.
        """
        _write_agent(agents_root, 'leaf', 'name: leaf\ndescription: d\ntools: [read_file, web]')
        calls, _ = fake_factory
        await delegate(_ctx(), subagent_type='leaf', prompt='go')
        assert len(calls) == 1
        tool_filter = calls[0]['tool_filter']
        assert tool_filter == {'read_file', 'web'}
        assert 'delegate' not in tool_filter

    @pytest.mark.asyncio
    async def test_allowlist_with_delegate_keeps_it(self, agents_root: Path, fake_factory):
        """If an agent explicitly asks for ``delegate`` in its allowlist, it gets it.

        Used when building a coordinator subagent that orchestrates further
        delegations.
        """
        _write_agent(
            agents_root,
            'coordinator',
            'name: coordinator\ndescription: d\ntools: [delegate, read_file]',
        )
        calls, _ = fake_factory
        await delegate(_ctx(), subagent_type='coordinator', prompt='orchestrate')
        tool_filter = calls[0]['tool_filter']
        assert 'delegate' in tool_filter

    @pytest.mark.asyncio
    async def test_missing_tools_uses_role_default_minus_delegate(self, agents_root: Path, fake_factory):
        """Omitting ``tools`` frontmatter should produce the full admin pool minus delegate."""
        _write_agent(agents_root, 'defaults', 'name: defaults\ndescription: d')
        calls, _ = fake_factory
        await delegate(_ctx(role='admin'), subagent_type='defaults', prompt='go')
        tool_filter = calls[0]['tool_filter']
        assert 'bash' in tool_filter
        assert 'read_file' in tool_filter
        assert 'marcel' in tool_filter
        # Recursion guard still applies to the default pool path
        assert 'delegate' not in tool_filter

    @pytest.mark.asyncio
    async def test_disallowed_tools_subtracted(self, agents_root: Path, fake_factory):
        """``disallowed_tools`` removes tools from the allowlist after resolution."""
        _write_agent(
            agents_root,
            'safe',
            'name: safe\ndescription: d\ntools: [bash, read_file, web]\ndisallowed_tools: [bash]',
        )
        calls, _ = fake_factory
        await delegate(_ctx(), subagent_type='safe', prompt='go')
        tool_filter = calls[0]['tool_filter']
        assert 'bash' not in tool_filter
        assert tool_filter == {'read_file', 'web'}

    @pytest.mark.asyncio
    async def test_user_role_default_pool_has_no_admin_tools(self, agents_root: Path, fake_factory):
        """A user-role parent produces a user-role default pool in the subagent.

        This is downstream enforcement — the create_marcel_agent role gate
        also enforces it, but we verify the delegate-computed pool is
        already sane.
        """
        _write_agent(agents_root, 'defaults', 'name: defaults\ndescription: d')
        calls, _ = fake_factory
        await delegate(_ctx(role='user'), subagent_type='defaults', prompt='go')
        tool_filter = calls[0]['tool_filter']
        assert 'bash' not in tool_filter
        assert 'delegate' not in tool_filter
        assert 'marcel' in tool_filter


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


class TestModelResolution:
    @pytest.mark.asyncio
    async def test_inherit_uses_parent_model(self, agents_root: Path, fake_factory):
        _write_agent(agents_root, 'inh', 'name: inh\ndescription: d\nmodel: inherit')
        calls, _ = fake_factory
        await delegate(
            _ctx(model='openai:gpt-4o'),
            subagent_type='inh',
            prompt='go',
        )
        assert calls[0]['model'] == 'openai:gpt-4o'

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_parent(self, agents_root: Path, fake_factory):
        _write_agent(
            agents_root,
            'haiku',
            'name: haiku\ndescription: d\nmodel: anthropic:claude-haiku-4-5-20251001',
        )
        calls, _ = fake_factory
        await delegate(
            _ctx(model='openai:gpt-4o'),
            subagent_type='haiku',
            prompt='go',
        )
        assert calls[0]['model'] == 'anthropic:claude-haiku-4-5-20251001'

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_parent_has_none(self, agents_root: Path, fake_factory):
        from marcel_core.harness.agent import DEFAULT_MODEL

        _write_agent(agents_root, 'inh', 'name: inh\ndescription: d\nmodel: inherit')
        calls, _ = fake_factory
        await delegate(_ctx(model=None), subagent_type='inh', prompt='go')
        assert calls[0]['model'] == DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Fresh context isolation
# ---------------------------------------------------------------------------


class TestFreshContext:
    @pytest.mark.asyncio
    async def test_subagent_gets_fresh_turn_state(self, agents_root: Path, fake_factory):
        """Per-turn flags on the parent must not leak into the subagent deps."""
        _write_agent(agents_root, 'a', 'name: a\ndescription: d')
        calls, fake_ref = fake_factory

        ctx = _ctx()
        # Simulate parent having already notified and done some web searches
        ctx.deps.turn.notified = True
        ctx.deps.turn.web_search_count = 3

        await delegate(ctx, subagent_type='a', prompt='go')

        sub_deps = fake_ref['current'].run_calls[0]['deps']
        assert sub_deps.turn.notified is False
        assert sub_deps.turn.web_search_count == 0

    @pytest.mark.asyncio
    async def test_conversation_id_is_derived(self, agents_root: Path, fake_factory):
        _write_agent(agents_root, 'a', 'name: a\ndescription: d')
        _, fake_ref = fake_factory
        await delegate(_ctx(), subagent_type='a', prompt='go')
        sub_deps = fake_ref['current'].run_calls[0]['deps']
        assert sub_deps.conversation_id.startswith('conv-1:delegate:a')

    @pytest.mark.asyncio
    async def test_system_prompt_is_agent_body_only(self, agents_root: Path, fake_factory):
        _write_agent(
            agents_root,
            'a',
            'name: a\ndescription: d',
            body='You are the specialist. Focus on one thing.',
        )
        calls, _ = fake_factory
        await delegate(_ctx(), subagent_type='a', prompt='go')
        sp = calls[0]['system_prompt']
        assert 'You are the specialist' in sp
        # Must not have pulled in MARCEL.md / memory / channel guidance
        assert '# Marcel' not in sp
        assert 'Memory' not in sp

    @pytest.mark.asyncio
    async def test_returns_result_output_as_string(self, agents_root: Path, fake_factory):
        _write_agent(agents_root, 'a', 'name: a\ndescription: d')
        _, fake_ref = fake_factory
        # Override the fake's output by patching after the fact
        result = await delegate(_ctx(), subagent_type='a', prompt='go')
        # The fake_factory default output is "fake result"
        assert result == 'fake result'


# ---------------------------------------------------------------------------
# Usage limits
# ---------------------------------------------------------------------------


class TestUsageLimits:
    @pytest.mark.asyncio
    async def test_max_requests_passed_to_run(self, agents_root: Path, fake_factory):
        _write_agent(agents_root, 'bounded', 'name: bounded\ndescription: d\nmax_requests: 7')
        _, fake_ref = fake_factory
        await delegate(_ctx(), subagent_type='bounded', prompt='go')
        limits = fake_ref['current'].run_calls[0]['usage_limits']
        assert limits is not None
        assert limits.request_limit == 7

    @pytest.mark.asyncio
    async def test_no_max_requests_means_no_limits(self, agents_root: Path, fake_factory):
        _write_agent(agents_root, 'unlimited', 'name: unlimited\ndescription: d')
        _, fake_ref = fake_factory
        await delegate(_ctx(), subagent_type='unlimited', prompt='go')
        assert fake_ref['current'].run_calls[0]['usage_limits'] is None
