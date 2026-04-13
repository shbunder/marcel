"""Tests for harness/agent.py — model registry and agent creation."""

from __future__ import annotations

import pytest

from marcel_core.harness.agent import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    all_models,
    create_marcel_agent,
)


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
    from ``os.environ`` based on the ``provider:`` prefix.
    """

    @pytest.fixture(autouse=True)
    def fake_api_key(self, monkeypatch):
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')

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
