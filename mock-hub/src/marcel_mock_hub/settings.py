"""Settings the mock hub reads from its environment."""

from __future__ import annotations

import os

DEFAULT_PORT = 7499


def port(environ: dict[str, str] | None = None) -> int:
    """MOCK_HUB_PORT, else 7499, so two sessions on one Mac can each run their own mock hub."""
    value = (os.environ if environ is None else environ).get('MOCK_HUB_PORT', '').strip()
    if not value:
        return DEFAULT_PORT
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise SystemExit(f'MOCK_HUB_PORT must be a port number from 1 to 65535, not "{value}".')
    return int(value)
