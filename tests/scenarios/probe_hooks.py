"""Per-test hooks for the scenario probe park.

The probe park's server runs in-process, so tests mutate HANDLERS to script a
tool's behavior and inspect what it received — the connector-era replacement
for registering throwaway @marcel_tool probes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

HANDLERS: dict[str, Callable[..., Awaitable[str] | str]] = {}


def reset() -> None:
    HANDLERS.clear()
