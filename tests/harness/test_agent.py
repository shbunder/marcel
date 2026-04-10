"""Tests for harness/agent.py — model selection and agent creation."""

from __future__ import annotations

import pytest

from marcel_core.config import settings
from marcel_core.harness.agent import (
    ANTHROPIC_MODELS,
    DEFAULT_MODEL,
    OPENAI_MODELS,
    _create_anthropic_model,
    all_models,
    create_marcel_agent,
)


class TestAllModels:
    def test_returns_dict(self):
        result = all_models()
        assert isinstance(result, dict)

    def test_contains_anthropic_and_openai(self):
        result = all_models()
        for key in ANTHROPIC_MODELS:
            assert key in result
        for key in OPENAI_MODELS:
            assert key in result


class TestCreateAnthropicModel:
    def test_uses_bedrock_when_aws_region_set(self, monkeypatch):
        monkeypatch.setattr(settings, 'aws_region', 'eu-west-1')
        result = _create_anthropic_model('claude-sonnet-4-6')
        assert result.startswith('bedrock:')

    def test_uses_anthropic_api_key_when_set(self, monkeypatch):
        monkeypatch.setattr(settings, 'aws_region', None)
        monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-ant-test')
        monkeypatch.setattr(settings, 'openai_api_key', None)
        result = _create_anthropic_model('claude-sonnet-4-6')
        assert result == 'anthropic:claude-sonnet-4-6'

    def test_uses_openai_for_openai_model(self, monkeypatch):
        monkeypatch.setattr(settings, 'aws_region', None)
        monkeypatch.setattr(settings, 'anthropic_api_key', None)
        monkeypatch.setattr(settings, 'openai_api_key', 'sk-oai-test')
        result = _create_anthropic_model('gpt-4o')
        assert result == 'openai:gpt-4o'

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, 'aws_region', None)
        monkeypatch.setattr(settings, 'anthropic_api_key', None)
        monkeypatch.setattr(settings, 'openai_api_key', None)
        with pytest.raises(RuntimeError, match='No API key'):
            _create_anthropic_model('claude-sonnet-4-6')

    def test_openai_fallback_when_openai_key_only(self, monkeypatch):
        monkeypatch.setattr(settings, 'aws_region', None)
        monkeypatch.setattr(settings, 'anthropic_api_key', None)
        monkeypatch.setattr(settings, 'openai_api_key', 'sk-oai-test')
        # Anthropic model with only openai key → falls through to openai fallback
        result = _create_anthropic_model('claude-sonnet-4-6')
        assert result.startswith('openai:')


class TestCreateMarcelAgent:
    """Test agent creation — pydantic-ai reads ANTHROPIC_API_KEY from os.environ directly."""

    @pytest.fixture(autouse=True)
    def fake_api_key(self, monkeypatch):
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')
        monkeypatch.setattr(settings, 'aws_region', None)
        monkeypatch.setattr(settings, 'anthropic_api_key', 'sk-ant-test-fake')

    def test_creates_user_agent(self):
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='user')
        assert agent is not None

    def test_creates_admin_agent(self):
        agent = create_marcel_agent(system_prompt='You are a test assistant.', role='admin')
        assert agent is not None

    def test_strips_provider_prefix(self):
        agent = create_marcel_agent(
            model=f'anthropic:{DEFAULT_MODEL}',
            system_prompt='Test',
            role='user',
        )
        assert agent is not None

    def test_default_system_prompt_used_when_empty(self):
        # Should not raise even with empty system_prompt
        agent = create_marcel_agent(system_prompt='', role='user')
        assert agent is not None
