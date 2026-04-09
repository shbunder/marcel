"""Tests for the Claude Code OAuth token loader."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from marcel_core.harness.oauth import load_oauth_token


def _write_credentials(tmp_path: Path, access_token: str, expires_offset_s: int = 3600) -> Path:
    """Write a fake credentials file and return its path."""
    expires_at_ms = int((time.time() + expires_offset_s) * 1000)
    data = {
        'claudeAiOauth': {
            'accessToken': access_token,
            'refreshToken': 'sk-ant-ort01-fake',
            'expiresAt': expires_at_ms,
            'scopes': ['user:inference'],
        }
    }
    cred_file = tmp_path / '.credentials.json'
    cred_file.write_text(json.dumps(data))
    return cred_file


# ---------------------------------------------------------------------------
# load_oauth_token
# ---------------------------------------------------------------------------


def test_load_token_returns_access_token(tmp_path):
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-test')
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file):
        token = load_oauth_token()
    assert token == 'sk-ant-oat01-test'


def test_load_token_missing_file(tmp_path):
    missing = tmp_path / 'no-such-file.json'
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', missing):
        token = load_oauth_token()
    assert token is None


def test_load_token_malformed_json(tmp_path):
    bad = tmp_path / '.credentials.json'
    bad.write_text('not json')
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', bad):
        token = load_oauth_token()
    assert token is None


def test_load_token_missing_access_token_key(tmp_path):
    cred_file = tmp_path / '.credentials.json'
    cred_file.write_text(json.dumps({'claudeAiOauth': {}}))
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file):
        token = load_oauth_token()
    assert token is None


def test_load_token_expired_still_returns_token(tmp_path, caplog):
    """Expired token is still returned (API call may fail, but we let that surface naturally)."""
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-expired', expires_offset_s=-100)
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file):
        import logging

        with caplog.at_level(logging.WARNING, logger='marcel_core.harness.oauth'):
            token = load_oauth_token()
    assert token == 'sk-ant-oat01-expired'
    assert 'expired' in caplog.text.lower()


def test_load_token_near_expiry_warns(tmp_path, caplog):
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-soon', expires_offset_s=60)
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file):
        import logging

        with caplog.at_level(logging.WARNING, logger='marcel_core.harness.oauth'):
            token = load_oauth_token()
    assert token == 'sk-ant-oat01-soon'
    assert 'expires in' in caplog.text


# ---------------------------------------------------------------------------
# build_anthropic_provider
# ---------------------------------------------------------------------------


def test_build_provider_uses_oauth_token(tmp_path):
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-build-test')

    mock_anthropic = MagicMock()
    mock_provider = MagicMock()
    mock_model = MagicMock()

    with (
        patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file),
        patch('marcel_core.harness.oauth.AsyncAnthropic', mock_anthropic),
        patch('marcel_core.harness.oauth.AnthropicProvider', mock_provider),
        patch('marcel_core.harness.oauth.AnthropicModel', mock_model),
    ):
        from marcel_core.harness import oauth

        oauth.build_anthropic_provider('claude-sonnet-4-6')

    call_kwargs = mock_anthropic.call_args.kwargs
    assert call_kwargs['auth_token'] == 'sk-ant-oat01-build-test'
    assert 'http_client' in call_kwargs  # beta headers injected via custom client
    mock_model.assert_called_once_with('claude-sonnet-4-6', provider=mock_provider.return_value)


def test_build_provider_raises_without_credentials(tmp_path):
    missing = tmp_path / 'no-creds.json'
    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', missing):
        from marcel_core.harness import oauth

        with pytest.raises(RuntimeError, match='No Claude Code OAuth token'):
            oauth.build_anthropic_provider('claude-sonnet-4-6')


# ---------------------------------------------------------------------------
# _create_anthropic_model priority
# ---------------------------------------------------------------------------


def test_agent_prefers_api_key_over_oauth(tmp_path, monkeypatch):
    """ANTHROPIC_API_KEY takes precedence over OAuth token."""
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-should-not-use')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    monkeypatch.delenv('AWS_REGION', raising=False)

    with patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file):
        from marcel_core.harness.agent import _create_anthropic_model

        result = _create_anthropic_model('claude-sonnet-4-6')

    assert result == 'anthropic:claude-sonnet-4-6'


def test_agent_falls_back_to_oauth_when_no_api_key(tmp_path, monkeypatch):
    """Falls back to OAuth when ANTHROPIC_API_KEY is not set."""
    cred_file = _write_credentials(tmp_path, 'sk-ant-oat01-fallback')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('AWS_REGION', raising=False)

    mock_anthropic = MagicMock()
    mock_provider = MagicMock()
    mock_model_cls = MagicMock()

    with (
        patch('marcel_core.harness.oauth._CREDENTIALS_FILE', cred_file),
        patch('marcel_core.harness.oauth.AsyncAnthropic', mock_anthropic),
        patch('marcel_core.harness.oauth.AnthropicProvider', mock_provider),
        patch('marcel_core.harness.oauth.AnthropicModel', mock_model_cls),
    ):
        import importlib

        from marcel_core.harness import agent as agent_mod

        importlib.reload(agent_mod)  # reload to pick up monkeypatched env
        agent_mod._create_anthropic_model('claude-sonnet-4-6')

    call_kwargs = mock_anthropic.call_args.kwargs
    assert call_kwargs['auth_token'] == 'sk-ant-oat01-fallback'
    assert 'http_client' in call_kwargs  # beta headers injected via custom client


def test_agent_prefers_bedrock_over_all(tmp_path, monkeypatch):
    """AWS_REGION takes highest priority."""
    monkeypatch.setenv('AWS_REGION', 'eu-west-1')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-also-set')

    from marcel_core.harness.agent import _create_anthropic_model

    result = _create_anthropic_model('claude-sonnet-4-6')

    assert isinstance(result, str)
    assert result.startswith('bedrock:')
