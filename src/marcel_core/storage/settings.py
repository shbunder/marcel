"""Per-user, per-channel settings storage.

Settings are stored in ``~/.marcel/users/{slug}/settings.json``.
Currently tracks: the preferred model per channel.
"""

from __future__ import annotations

import json
import logging
import pathlib

from pydantic import BaseModel

from ._atomic import atomic_write
from ._root import data_root

log = logging.getLogger(__name__)

_SETTINGS_FILE = 'settings.json'


class UserSettings(BaseModel):
    """Typed schema for per-user settings.json."""

    channel_models: dict[str, str] = {}


def _settings_path(user_slug: str) -> pathlib.Path:
    return data_root() / 'users' / user_slug / _SETTINGS_FILE


def _load_settings(user_slug: str) -> UserSettings:
    path = _settings_path(user_slug)
    if not path.exists():
        return UserSettings()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        return UserSettings.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        log.warning('Could not read settings for user %s; returning defaults', user_slug)
        return UserSettings()


def _save_settings(user_slug: str, data: UserSettings) -> None:
    path = _settings_path(user_slug)
    atomic_write(path, data.model_dump_json(indent=2), mode=0o600)


def load_channel_model(user_slug: str, channel: str) -> str | None:
    """Return the preferred model for a user/channel pair, or None if not set.

    Args:
        user_slug: The user's short identifier (e.g. ``"shaun"``).
        channel: The channel name (e.g. ``"telegram"``, ``"cli"``).

    Returns:
        Model name string, or ``None`` if no preference is stored.
    """
    settings = _load_settings(user_slug)
    return settings.channel_models.get(channel)


def save_channel_model(user_slug: str, channel: str, model: str) -> None:
    """Persist a model preference for a user/channel pair.

    Args:
        user_slug: The user's short identifier.
        channel: The channel name.
        model: The model name to store (e.g. ``"claude-sonnet-4-6"``).
    """
    settings = _load_settings(user_slug)
    settings.channel_models[channel] = model
    _save_settings(user_slug, settings)
    log.info('Saved model preference: user=%s channel=%s model=%s', user_slug, channel, model)
