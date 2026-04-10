"""Root-level pytest configuration.

Resets environment variables that are set in .env.local but would
break test isolation if they were loaded at import time.
"""

import pytest


@pytest.fixture(autouse=True)
def reset_settings_for_tests(monkeypatch):
    """Reset settings singleton fields that .env.local may override.

    pydantic-settings loads .env.local once at import time into a singleton.
    monkeypatch.setenv/delenv only affects os.environ — it does not re-read
    the singleton. Patch the singleton fields directly so individual tests
    start from a clean baseline and can override with monkeypatch.setattr.
    """
    from marcel_core.config import settings

    monkeypatch.delenv('MARCEL_API_TOKEN', raising=False)
    monkeypatch.setattr(settings, 'marcel_api_token', '')
    monkeypatch.setattr(settings, 'telegram_webhook_secret', '')
    monkeypatch.setattr(settings, 'marcel_public_url', None)
