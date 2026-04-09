"""Per-user, per-channel settings storage.

Settings are stored in ``~/.marcel/users/{slug}/settings.json``.
Currently tracks: the preferred model per channel.
"""

from __future__ import annotations

import json
import logging
import pathlib

from ._atomic import atomic_write
from ._root import data_root

log = logging.getLogger(__name__)

_SETTINGS_FILE = 'settings.json'


def _settings_path(user_slug: str) -> pathlib.Path:
    return data_root() / 'users' / user_slug / _SETTINGS_FILE


def _load_settings(user_slug: str) -> dict:
    path = _settings_path(user_slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        log.warning('Could not read settings for user %s; returning defaults', user_slug)
        return {}


def _save_settings(user_slug: str, data: dict) -> None:
    path = _settings_path(user_slug)
    atomic_write(path, json.dumps(data, indent=2), mode=0o600)


def load_channel_model(user_slug: str, channel: str) -> str | None:
    """Return the preferred model for a user/channel pair, or None if not set.

    Args:
        user_slug: The user's short identifier (e.g. ``"shaun"``).
        channel: The channel name (e.g. ``"telegram"``, ``"cli"``).

    Returns:
        Model name string, or ``None`` if no preference is stored.
    """
    settings = _load_settings(user_slug)
    return settings.get('channel_models', {}).get(channel)


def save_channel_model(user_slug: str, channel: str, model: str) -> None:
    """Persist a model preference for a user/channel pair.

    Args:
        user_slug: The user's short identifier.
        channel: The channel name.
        model: The model name to store (e.g. ``"claude-sonnet-4-6"``).
    """
    settings = _load_settings(user_slug)
    if 'channel_models' not in settings:
        settings['channel_models'] = {}
    settings['channel_models'][channel] = model
    _save_settings(user_slug, settings)
    log.info('Saved model preference: user=%s channel=%s model=%s', user_slug, channel, model)
