"""Configuration loader for the Marcel CLI.

Reads ``~/.marcel/config.toml`` and applies CLI flag overrides.
Default values are used when the config file does not exist.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomllib  # type: ignore[import]  # backport on 3.10

_CONFIG_PATH = Path.home() / '.marcel' / 'config.toml'

_DEFAULTS: dict = {
    'host': 'localhost',
    'port': 8000,
    'user': 'shaun',
    'token': '',
    'model': 'claude-sonnet-4-6',
}


@dataclass
class Config:
    """Resolved Marcel CLI configuration.

    Attributes:
        host: Marcel server hostname.
        port: Marcel server port.
        user: User slug sent with each message.
        token: Long-lived auth token (not validated by server in Phase 1).
    """

    host: str
    port: int
    user: str
    token: str
    model: str

    @property
    def ws_url(self) -> str:
        """WebSocket URL for the chat endpoint."""
        return f'ws://{self.host}:{self.port}/ws/chat'


def load_config(
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    model: str | None = None,
) -> Config:
    """Load config from ``~/.marcel/config.toml``, then apply overrides.

    Creates a default config file if none exists.

    Args:
        host: Override for the server hostname.
        port: Override for the server port.
        user: Override for the user slug.

    Returns:
        Resolved :class:`Config` instance.
    """
    raw: dict = dict(_DEFAULTS)

    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open('rb') as fh:
            raw.update(tomllib.load(fh))
    else:
        _write_default_config()

    if host is not None:
        raw['host'] = host
    if port is not None:
        raw['port'] = port
    if user is not None:
        raw['user'] = user
    if model is not None:
        raw['model'] = model

    return Config(
        host=str(raw.get('host', _DEFAULTS['host'])),
        port=int(raw.get('port', _DEFAULTS['port'])),
        user=str(raw.get('user', _DEFAULTS['user'])),
        token=str(raw.get('token', _DEFAULTS['token'])),
        model=str(raw.get('model', _DEFAULTS['model'])),
    )


def _write_default_config() -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        '# Marcel server address\n'
        'host = "localhost"\n'
        'port = 8000\n\n'
        '# Your user slug\n'
        'user = "shaun"\n\n'
        '# Long-lived developer token (auth not yet enforced in Phase 1)\n'
        'token = ""\n\n'
        '# Claude model to use\n'
        'model = "claude-sonnet-4-6"\n',
        encoding='utf-8',
    )
