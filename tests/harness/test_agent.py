"""Tests for harness/agent.py — model registry and agent creation."""

from __future__ import annotations

import pytest
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

from marcel_core.config import settings
from marcel_core.harness.agent import (
    KNOWN_MODELS,
    _build_local_model,
    all_models,
    available_tool_names,
    create_marcel_agent,
    default_model,
)
from marcel_core.harness.context import MarcelDeps


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
        model_id = default_model()
        assert ':' in model_id
        provider, _, model = model_id.partition(':')
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
        """User role: system prompt is wired in, user-visible tools registered,
        admin-only tools absent."""
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='user')
        assert agent._instructions == ['You are a test assistant.']
        names = _registered_tool_names(agent)
        assert 'toolkit' not in names  # dispatcher retired (FEAT-260718-c232d9)
        assert 'marcel' in names
        assert 'bash' not in names
        assert 'delegate' not in names

    def test_creates_admin_agent(self):
        """Admin role: same plumbing, plus the admin-only power tools."""
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='admin')
        assert agent._instructions == ['You are a test assistant.']
        names = _registered_tool_names(agent)
        # bash/read_file & co. are capabilities now (FEAT-260718-38235c),
        # and delegate is the SubAgents capability's tool (FEAT-260718-b6d1da);
        # the registry keeps the remaining admin power tools.
        assert 'delegate' not in names
        assert 'claude_code' in names
        assert 'git_status' in names

    def test_accepts_explicit_qualified_model(self):
        """An explicit model string overrides the default and reaches pydantic-ai."""
        agent = create_marcel_agent(
            model='anthropic:claude-sonnet-4-6',
            system_prompt='Test',
            role='user',
        )
        assert isinstance(agent.model, Model)
        assert agent.model.model_name == 'claude-sonnet-4-6'

    def test_default_system_prompt_used_when_empty(self):
        """Empty system prompt falls back to the built-in Marcel description."""
        agent = create_marcel_agent(system_prompt='', role='user')
        assert agent._instructions, 'default prompt should be non-empty'
        first = agent._instructions[0]
        assert isinstance(first, str) and 'Marcel' in first


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
        assert result.model_name == 'qwen3.5:4b-instruct-q4_K_M'

    def test_create_agent_accepts_local_string(self, monkeypatch):
        """``local:*`` strings build an OpenAIChatModel and wire it into the agent."""
        monkeypatch.setattr(settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        agent = create_marcel_agent(
            model='local:qwen3.5:4b',
            system_prompt='Test',
            role='user',
        )
        assert isinstance(agent.model, OpenAIChatModel)
        assert agent.model.model_name == 'qwen3.5:4b'

    def test_create_agent_local_raises_when_url_unset(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_local_llm_url', None)
        with pytest.raises(RuntimeError, match='MARCEL_LOCAL_LLM_URL'):
            create_marcel_agent(
                model='local:qwen3.5:4b',
                system_prompt='Test',
                role='user',
            )

    def test_non_local_string_unchanged(self):
        """Non-``local:`` qualified strings must still pass through verbatim.

        The agent's resolved model must be the AnthropicModel built from the
        original string, not an OpenAIChatModel (which is only for ``local:*``).
        """
        agent = create_marcel_agent(
            model='anthropic:claude-sonnet-4-6',
            system_prompt='Test',
            role='user',
        )
        assert isinstance(agent.model, Model)
        assert not isinstance(agent.model, OpenAIChatModel)
        assert agent.model.model_name == 'claude-sonnet-4-6'


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
        # Shell/FileSystem successors are capability tools but stay in the
        # role pool so delegate frontmatter can grant them by name.
        assert 'run_command' in names
        assert 'read_file' in names
        assert 'claude_code' in names
        # delegate is granted via the subagents build flag, never the pool —
        # a child's default pool must not carry it (recursion rule).
        assert 'delegate' not in names


_REGISTRY_BUNDLE_IDS = frozenset({'dev-tools', 'marketplace-tools', 'utility-tools', 'job-tools'})


def _registered_tool_names(agent) -> set[str]:
    """Introspect the registry-side tool names on a pydantic-ai Agent.

    Since FEAT-260721-51f9e3 the registry tools attach as domain-owned
    ``Capability`` tool bundles (dev/marketplace/utility/job) instead of a
    hand-built ``FunctionToolset``. This helper keeps its original
    semantics — the *registry-side* names only — by reading exactly those
    bundles; web/shell/filesystem are separate capabilities asserted by
    their own helpers. Not pydantic-ai public API but stable enough for
    assertions; kept in one helper so a bump touches one place.
    """
    from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

    names: set[str] = set()
    for cap in agent.root_capability.capabilities:
        if getattr(cap, 'id', None) not in _REGISTRY_BUNDLE_IDS:
            continue
        current = cap.get_toolset()
        while isinstance(current, WrapperToolset):
            current = current.wrapped
        if isinstance(current, FunctionToolset):
            names |= set(current.tools.keys())
    return names


def _has_web_capability(agent) -> bool:
    """web is a non-deferred capability now (ADR-260720-9318b1), not a
    FunctionToolset entry — check the composed capability list."""
    return any(getattr(c, 'id', None) == 'web-tools' for c in agent.root_capability.capabilities)


class TestToolFilter:
    """ISSUE-074: ``tool_filter`` restricts which tools a created agent exposes."""

    def test_filter_none_registers_full_role_pool(self):
        agent = create_marcel_agent(system_prompt='t', role='admin')
        names = _registered_tool_names(agent)
        # Admin should get the full registry pool (shell/file tools, delegate
        # and web are capability-provided, asserted at composition level)
        assert 'git_status' in names
        assert 'web' not in names
        assert _has_web_capability(agent)

    def test_empty_filter_registers_no_tools(self):
        agent = create_marcel_agent(system_prompt='t', role='admin', tool_filter=set())
        assert _registered_tool_names(agent) == set()

    def test_filter_restricts_to_exact_allowlist(self):
        agent = create_marcel_agent(
            system_prompt='t',
            role='admin',
            tool_filter={'web', 'git_status'},
        )
        # web is capability-provided (filter still gates it on); the registry
        # side of the filter yields git_status alone.
        assert _registered_tool_names(agent) == {'git_status'}
        assert _has_web_capability(agent)

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
        # The admin-only tools are stripped regardless of allowlist content;
        # web survives as its capability (all-users), leaving the registry
        # FunctionToolset empty.
        assert _registered_tool_names(agent) == set()
        assert _has_web_capability(agent)

    def test_user_role_default_pool_has_no_admin_tools(self):
        agent = create_marcel_agent(system_prompt='t', role='user')
        names = _registered_tool_names(agent)
        assert 'bash' not in names
        assert 'delegate' not in names
        assert 'claude_code' not in names
        assert 'marcel' in names
        assert 'toolkit' not in names  # dispatcher retired (FEAT-260718-c232d9)


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


def _deps() -> MarcelDeps:
    return MarcelDeps(user_slug='alice', conversation_id='c-1', channel='cli', role='user')


class TestModelInstanceSeam:
    """create_marcel_agent accepts a Model instance (STORY-260706-727779).

    The instance passes to ``Agent()`` verbatim: no provider inference, no
    API key required — the seam ``marcel_testing`` drives scenarios through.
    """

    def test_instance_builds_agent_without_api_keys(self, monkeypatch):
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        agent = create_marcel_agent(model=TestModel(call_tools=[]), system_prompt='hi', role='user')
        assert agent is not None

    def test_instance_agent_runs_deterministically(self):
        agent = create_marcel_agent(
            model=TestModel(custom_output_text='scripted reply', call_tools=[]),
            system_prompt='You are a test.',
            role='user',
        )
        result = agent.run_sync('hello', deps=_deps())
        assert result.output == 'scripted reply'


class TestBundleRegistryParity:
    """FEAT-260721-51f9e3: the domain bundles expose byte-identical name sets
    to the retired FunctionToolset loop, for every role/filter combination."""

    def _expected(self, role: str, tool_filter: set[str] | None) -> set[str]:
        from marcel_core.harness.agent import _TOOL_REGISTRY

        return {
            name
            for name, _fn, required in _TOOL_REGISTRY
            if (required != 'admin' or role == 'admin') and (tool_filter is None or name in tool_filter)
        }

    def test_parity_across_roles_and_filters(self):
        cases = [
            ('admin', None),
            ('user', None),
            ('admin', set()),
            ('admin', {'git_status', 'marcel', 'create_job'}),
            ('user', {'git_status', 'marcel', 'create_job'}),  # role gate beats allowlist
            ('user', {'marketplace'}),  # admin bundle never composes for user
        ]
        for role, tool_filter in cases:
            agent = create_marcel_agent(system_prompt='t', role=role, tool_filter=tool_filter)
            assert _registered_tool_names(agent) == self._expected(role, tool_filter), (role, tool_filter)

    def test_aggregate_registry_order_stable(self):
        """delegate frontmatter + docs reference names by the aggregate; the
        domain split must not lose or rename any."""
        from marcel_core.harness.agent import _TOOL_REGISTRY

        names = [name for name, _fn, _r in _TOOL_REGISTRY]
        assert names == [
            'git_status',
            'git_diff',
            'git_log',
            'git_add',
            'git_commit',
            'git_push',
            'claude_code',
            'marketplace',
            'generate_chart',
            'marcel',
            'create_job',
            'list_jobs',
            'get_job',
            'update_job',
            'delete_job',
            'run_job_now',
            'job_templates',
            'job_cache_write',
            'job_cache_read',
        ]
        assert len(names) == len(set(names)), 'no duplicate tool names across domains'


class TestBundleRoleColumnIsTheGate:
    """Finding from FEAT-260721-51f9e3 verification: the role_required column
    itself must be layer 1 — an 'admin'-declared triple in an otherwise
    all-user list must never attach for a user, regardless of bundle
    placement in composition."""

    def test_admin_triple_in_all_user_bundle_never_attaches_for_user(self):
        from marcel_core.tools.capability import build_tool_bundle

        def fake_admin_tool():  # pragma: no cover - never called
            return 'secret'

        def fake_user_tool():
            return 'ok'

        mixed = [
            ('fake_admin_tool', fake_admin_tool, 'admin'),
            ('fake_user_tool', fake_user_tool, None),
        ]
        user_bundle = build_tool_bundle('test-bundle', 'test', mixed, 'user', None)
        assert user_bundle is not None
        assert [t.__name__ for t in [fn for fn in _bundle_functions(user_bundle)]] == ['fake_user_tool']

        admin_bundle = build_tool_bundle('test-bundle', 'test', mixed, 'admin', None)
        assert admin_bundle is not None
        assert {t.__name__ for t in _bundle_functions(admin_bundle)} == {'fake_admin_tool', 'fake_user_tool'}

    def test_admin_triple_stripped_even_when_allowlisted(self):
        from marcel_core.tools.capability import build_tool_bundle

        def fake_admin_tool():  # pragma: no cover - never called
            return 'secret'

        bundle = build_tool_bundle(
            'test-bundle', 'test', [('fake_admin_tool', fake_admin_tool, 'admin')], 'user', {'fake_admin_tool'}
        )
        assert bundle is None, 'role gate must beat the allowlist inside the bundle itself'


def _bundle_functions(bundle):
    """Extract the plain functions a Capability tool bundle carries."""
    from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

    current = bundle.get_toolset()
    while isinstance(current, WrapperToolset):
        current = current.wrapped
    assert isinstance(current, FunctionToolset)
    return [tool.function for tool in current.tools.values()]
