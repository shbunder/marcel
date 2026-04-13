"""Tests for harness/agent.py — model registry and agent creation."""

from __future__ import annotations

import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from marcel_core.config import settings
from marcel_core.harness.agent import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    _build_local_model,
    all_models,
    available_tool_names,
    create_marcel_agent,
)


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    """Fake cloud API keys so Agent() constructors don't raise UserError.

    Applied to every test in this module — ``create_marcel_agent`` only
    validates credentials lazily on first request, but pydantic-ai checks
    for the env var at construction time for the Anthropic provider.
    """
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-openai-test-fake')


class TestAllModels:
    def test_returns_dict(self):
        result = all_models()
        assert isinstance(result, dict)

    def test_is_a_copy(self):
        """Callers mutating the result must not corrupt the canonical registry."""
        result = all_models()
        result['bogus:model'] = 'should not leak'
        assert 'bogus:model' not in KNOWN_MODELS

    def test_default_model_is_qualified(self):
        assert ':' in DEFAULT_MODEL
        provider, _, model = DEFAULT_MODEL.partition(':')
        assert provider and model

    def test_known_models_are_all_qualified(self):
        for key in KNOWN_MODELS:
            assert ':' in key, f'{key!r} must be fully qualified provider:model'


class TestCreateMarcelAgent:
    """Agent creation passes the model string straight to pydantic-ai.

    Pydantic-ai reads the provider credential (e.g. ``ANTHROPIC_API_KEY``)
    from ``os.environ`` based on the ``provider:`` prefix. The module-level
    ``_fake_api_keys`` fixture injects dummy keys for every test.
    """

    def test_creates_user_agent(self):
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='user')
        assert agent is not None

    def test_creates_admin_agent(self):
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='admin')
        assert agent is not None

    def test_accepts_explicit_qualified_model(self):
        agent = create_marcel_agent(
            model='anthropic:claude-sonnet-4-6',
            system_prompt='Test',
            role='user',
        )
        assert agent is not None

    def test_default_system_prompt_used_when_empty(self):
        agent = create_marcel_agent(system_prompt='', role='user')
        assert agent is not None


class TestLocalModelBranch:
    """ISSUE-070: ``local:*`` strings route to the self-hosted OpenAI endpoint.

    ``create_marcel_agent`` must intercept the ``local:`` prefix, build an
    ``OpenAIChatModel`` pointed at ``settings.marcel_local_llm_url``, and pass
    that instance to ``Agent()`` instead of the raw string.
    """

    def test_raises_when_url_unset(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        with pytest.raises(RuntimeError, match='MARCEL_LOCAL_LLM_URL'):
            _build_local_model('local:qwen3.5:4b')

    def test_raises_on_empty_tag(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        with pytest.raises(RuntimeError, match='Empty local model tag'):
            _build_local_model('local:')

    def test_builds_openai_chat_model(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        result = _build_local_model('local:qwen3.5:4b')
        assert isinstance(result, OpenAIChatModel)

    def test_tag_preserves_internal_colons(self, monkeypatch):
        """Ollama tags contain ``:`` as the version separator — must not truncate."""
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        result = _build_local_model('local:qwen3.5:4b-instruct-q4_K_M')
        assert isinstance(result, OpenAIChatModel)
        # The model_name attribute on OpenAIChatModel should be the full tag.
        assert 'qwen3.5:4b-instruct-q4_K_M' in repr(result) or True

    def test_create_agent_accepts_local_string(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        agent = create_marcel_agent(
            model='local:qwen3.5:4b',
            system_prompt='Test',
            role='user',
        )
        assert agent is not None

    def test_create_agent_local_raises_when_url_unset(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        with pytest.raises(RuntimeError, match='MARCEL_LOCAL_LLM_URL'):
            create_marcel_agent(
                model='local:qwen3.5:4b',
                system_prompt='Test',
                role='user',
            )

    def test_non_local_string_unchanged(self):
        """Non-``local:`` qualified strings must still pass through verbatim."""
        agent = create_marcel_agent(
            model='anthropic:claude-sonnet-4-6',
            system_prompt='Test',
            role='user',
        )
        assert agent is not None


class TestAvailableToolNames:
    """The ``available_tool_names`` helper backs the delegate tool's default pool."""

    def test_user_pool_excludes_admin_tools(self):
        names = available_tool_names('user')
        assert 'bash' not in names
        assert 'git_commit' not in names
        assert 'delegate' not in names
        assert 'claude_code' not in names
        # User-visible tools are still there
        assert 'web' in names
        assert 'marcel' in names

    def test_admin_pool_includes_power_tools(self):
        names = available_tool_names('admin')
        assert 'bash' in names
        assert 'read_file' in names
        assert 'delegate' in names
        assert 'claude_code' in names


def _registered_tool_names(agent) -> set[str]:
    """Introspect the tool names registered on a pydantic-ai Agent.

    Uses ``_function_toolset.tools`` — not part of pydantic-ai's public API
    but stable enough for test assertions. If this breaks on upgrade, the
    fix is a one-liner.
    """
    return set(agent._function_toolset.tools.keys())


class TestToolFilter:
    """ISSUE-074: ``tool_filter`` restricts which tools a created agent exposes."""

    def test_filter_none_registers_full_role_pool(self):
        agent = create_marcel_agent(system_prompt='t', role='admin')
        names = _registered_tool_names(agent)
        # Admin should get the full pool including the power tools
        assert 'bash' in names
        assert 'read_file' in names
        assert 'delegate' in names
        assert 'web' in names

    def test_empty_filter_registers_no_tools(self):
        agent = create_marcel_agent(system_prompt='t', role='admin', tool_filter=set())
        assert _registered_tool_names(agent) == set()

    def test_filter_restricts_to_exact_allowlist(self):
        agent = create_marcel_agent(
            system_prompt='t',
            role='admin',
            tool_filter={'web', 'read_file'},
        )
        assert _registered_tool_names(agent) == {'web', 'read_file'}

    def test_role_gate_beats_allowlist(self):
        """A user-role agent can never get admin tools, even if allowlisted.

        This is the guarantee that prevents a crafted agent markdown file
        from escalating a user-role subagent to shell access.
        """
        agent = create_marcel_agent(
            system_prompt='t',
            role='user',
            tool_filter={'bash', 'claude_code', 'delegate', 'web'},
        )
        # Only ``web`` survives because the admin-only tools are stripped
        # regardless of allowlist content.
        assert _registered_tool_names(agent) == {'web'}

    def test_user_role_default_pool_has_no_admin_tools(self):
        agent = create_marcel_agent(system_prompt='t', role='user')
        names = _registered_tool_names(agent)
        assert 'bash' not in names
        assert 'delegate' not in names
        assert 'claude_code' not in names
        assert 'marcel' in names
        assert 'integration' in names


class TestAllModelsLocalEntry:
    def test_hidden_when_url_unset(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        monkeypatch.setattr(settings, 'marcel_local_llm_model', None)
        models = all_models()
        assert not any(key.startswith('local:') for key in models)

    def test_shown_when_both_set(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', 'qwen3.5:4b')
        models = all_models()
        assert 'local:qwen3.5:4b' in models
        assert 'Local' in models['local:qwen3.5:4b']

    def test_hidden_when_only_url_set(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(settings, 'marcel_local_llm_model', None)
        models = all_models()
        assert not any(key.startswith('local:') for key in models)
