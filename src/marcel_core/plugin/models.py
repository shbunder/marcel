"""Model registry access for plugin habitats.

Exposes the curated list of suggested models, the global default, and the
per-user / per-channel preferred-model setting. The settings habitat is the
canonical consumer; other habitats may use these helpers to render model
choices to the user without reaching into kernel internals.

This module is part of the stable plugin surface — the function names here
won't change between Marcel versions without a migration note. Habitats
should import from here, not from ``marcel_core.harness.agent`` or
``marcel_core.storage.settings``.

Example::

    from marcel_core.plugin import models

    for model_id, display_name in models.all_models().items():
        print(model_id, '—', display_name)

    current = models.get_channel_model(user_slug, 'telegram') or models.default_model()
    models.set_channel_model(user_slug, 'telegram', 'anthropic:claude-sonnet-4-6')
"""

from __future__ import annotations

from marcel_core.harness.agent import all_models, default_model
from marcel_core.storage.settings import (
    load_channel_model as get_channel_model,
    save_channel_model as set_channel_model,
)

__all__ = [
    'all_models',
    'default_model',
    'get_channel_model',
    'set_channel_model',
]
