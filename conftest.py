"""Root-level pytest configuration.

Resets environment variables that are set in .env.local but would
break test isolation if they were loaded at import time. Also loads
the zoo telegram habitat under the legacy ``marcel_core.channels.telegram``
namespace so kernel tests written before ISSUE-7d6b3f's migration can
keep patching/importing that path without modification.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys

import pytest


def _load_zoo_telegram_at_legacy_namespace() -> None:
    """Alias the zoo telegram habitat as ``marcel_core.channels.telegram``.

    After ISSUE-7d6b3f stage 4c, the telegram channel lives at
    ``<MARCEL_ZOO_DIR>/channels/telegram/``. Kernel tests still patch and
    import via the legacy ``marcel_core.channels.telegram.*`` path — loading
    the habitat under that name keeps them working unchanged.

    No-op when no zoo checkout is discoverable (tests that depend on
    telegram will fail with a clear ImportError rather than a silent
    skip).
    """
    if 'marcel_core.channels.telegram' in sys.modules:
        return

    candidates: list[pathlib.Path] = []
    env = os.environ.get('MARCEL_ZOO_DIR')
    if env:
        candidates.append(pathlib.Path(env).expanduser() / 'channels' / 'telegram')
    # Sibling checkout — the standard dev layout
    candidates.append(pathlib.Path(__file__).resolve().parent.parent / 'marcel-zoo' / 'channels' / 'telegram')
    # User's projects dir — fallback when running from /tmp or similar
    candidates.append(pathlib.Path('~/projects/marcel-zoo').expanduser() / 'channels' / 'telegram')

    habitat_dir = next((c for c in candidates if (c / '__init__.py').is_file()), None)
    if habitat_dir is None:
        return

    channels_pkg = importlib.import_module('marcel_core.channels')
    spec = importlib.util.spec_from_file_location(
        'marcel_core.channels.telegram',
        habitat_dir / '__init__.py',
        submodule_search_locations=[str(habitat_dir)],
    )
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules['marcel_core.channels.telegram'] = module
    channels_pkg.telegram = module  # type: ignore[attr-defined]
    spec.loader.exec_module(module)


_load_zoo_telegram_at_legacy_namespace()


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
