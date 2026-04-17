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
    # ISSUE-e0db47: session-level tier state, keyed by channel. Set by the
    # classifier on a session's first message and cleared on idle-summarise;
    # persists across turns within one session so tier selection is stable.
    channel_tiers: dict[str, str] = {}


def _settings_path(user_slug: str) -> pathlib.Path:
    return data_root() / 'users' / user_slug / _SETTINGS_FILE


def _load_settings(user_slug: str) -> UserSettings:
    path = _settings_path(user_slug)
    if not path.exists():
        return UserSettings()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        data = UserSettings.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        log.warning('Could not read settings for user %s; returning defaults', user_slug)
        return UserSettings()

    # Self-heal legacy unqualified model names (pre-ISSUE-073). Every pre-073
    # stored name was Anthropic, so prepend ``anthropic:`` and rewrite the file.
    migrated = False
    for channel, model in list(data.channel_models.items()):
        if ':' not in model:
            data.channel_models[channel] = f'anthropic:{model}'
            migrated = True
    if migrated:
        log.info('Migrated legacy unqualified model names for user=%s', user_slug)
        _save_settings(user_slug, data)
    return data


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
        model: Fully-qualified pydantic-ai model string
            (e.g. ``"anthropic:claude-sonnet-4-6"``, ``"openai:gpt-4o"``).
    """
    settings = _load_settings(user_slug)
    settings.channel_models[channel] = model
    _save_settings(user_slug, settings)
    log.info('Saved model preference: user=%s channel=%s model=%s', user_slug, channel, model)


def load_channel_tier(user_slug: str, channel: str) -> str | None:
    """Return the active session tier name for a user/channel pair, or None."""
    settings = _load_settings(user_slug)
    return settings.channel_tiers.get(channel)


def save_channel_tier(user_slug: str, channel: str, tier: str) -> None:
    """Persist the session tier for a user/channel pair."""
    settings = _load_settings(user_slug)
    settings.channel_tiers[channel] = tier
    _save_settings(user_slug, settings)
    log.info('Saved session tier: user=%s channel=%s tier=%s', user_slug, channel, tier)


def clear_channel_tier(user_slug: str, channel: str) -> None:
    """Clear the session tier so the next message re-classifies.

    Called on idle-summarise reset. No-op when no tier is stored.
    """
    settings = _load_settings(user_slug)
    if channel in settings.channel_tiers:
        del settings.channel_tiers[channel]
        _save_settings(user_slug, settings)
        log.info('Cleared session tier: user=%s channel=%s', user_slug, channel)
