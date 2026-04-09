"""Root-level pytest configuration.

Resets environment variables that are set in .env.local but would
break test isolation if they were loaded at import time.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def reset_auth_token_for_tests(monkeypatch):
    """Ensure MARCEL_API_TOKEN is unset during tests.

    main.py loads .env.local (which may set a real token) before tests run.
    Without this fixture, WebSocket tests that don't send a token would fail
    auth checks.
    """
    monkeypatch.delenv('MARCEL_API_TOKEN', raising=False)
