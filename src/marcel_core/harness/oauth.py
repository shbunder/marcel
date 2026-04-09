"""Claude Code OAuth token loader for the pydantic-ai harness.

Reads the OAuth access token that the ``claude`` CLI stores in
``~/.claude/.credentials.json`` and builds a configured
``AnthropicProvider`` that sends it as ``Authorization: Bearer``.

This lets the V2 harness reuse the same login session as Claude Code —
no separate ``ANTHROPIC_API_KEY`` needed.

Token lifecycle
---------------
Claude Code refreshes the token automatically when the CLI is used.
Since the V2 harness creates a new agent per turn, it re-reads the
credentials file on each call, picking up any refreshed token.
If the token is expired and Claude Code hasn't refreshed it yet, the
API call will fail with a 401 — the fix is to run ``claude`` once to
trigger a refresh.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

log = logging.getLogger(__name__)

_CREDENTIALS_FILE = Path.home() / '.claude' / '.credentials.json'

# Warn if token expires within this many seconds
_EXPIRY_WARN_SECS = 300


def load_oauth_token() -> str | None:
    """Read the Claude Code OAuth access token from disk.

    Returns:
        The access token string, or None if the credentials file is
        missing or malformed.
    """
    if not _CREDENTIALS_FILE.exists():
        return None

    try:
        data = json.loads(_CREDENTIALS_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug('Could not read Claude credentials: %s', exc)
        return None

    oauth = data.get('claudeAiOauth', {})
    token = oauth.get('accessToken')
    if not token:
        return None

    expires_at_ms = oauth.get('expiresAt', 0)
    if expires_at_ms:
        remaining = expires_at_ms / 1000 - time.time()
        if remaining < 0:
            log.warning('[oauth] Claude Code OAuth token has expired. Run `claude` to refresh it.')
        elif remaining < _EXPIRY_WARN_SECS:
            log.warning('[oauth] Claude Code OAuth token expires in %.0fs', remaining)

    return token


def build_anthropic_provider(model_name: str) -> AnthropicModel:
    """Build a pydantic-ai AnthropicModel using the Claude Code OAuth token.

    Returns an ``AnthropicModel`` instance configured with the OAuth token,
    or raises ``RuntimeError`` if no token is available.

    Args:
        model_name: The Anthropic model name (e.g. ``'claude-sonnet-4-6'``).

    Raises:
        RuntimeError: If the credentials file is missing or the token is absent.
    """
    token = load_oauth_token()
    if not token:
        raise RuntimeError('No Claude Code OAuth token found. Log in once with the `claude` CLI to authenticate.')

    log.info('[oauth] Using Claude Code OAuth token for model=%s', model_name)
    provider = AnthropicProvider(anthropic_client=AsyncAnthropic(auth_token=token))
    return AnthropicModel(model_name, provider=provider)
