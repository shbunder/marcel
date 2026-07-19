"""marcel-sdk — the versioned import wall for Marcel extensions.

An extension is a module exposing ``def register(marcel)`` that imports
**only** from this package, never from ``marcel_core`` internals
(ADR-260628-9f4c41). The kernel and extensions both depend on
``marcel_sdk``; it is the compatibility boundary the kernel can version
independently of its own internals.

``marcel_sdk`` has its own :data:`__version__`, decoupled from the
``marcel-core`` kernel version: a change to kernel internals that keeps
this surface intact does not bump the SDK; a change to *this* surface
does, with a migration note. That is the whole point of the wall.

Surface:

- **Contracts** (pure, no kernel dependency): the ``register`` object
  :class:`ExtensionAPI`, :class:`ToolResult`, and the lifecycle event
  bus (:class:`EventBus`, :class:`EventContext`, and the event types).
  Imported eagerly.
- **Helpers** (kernel-backed): ``credentials``, ``paths``, ``models``,
  ``rss``, ``get_logger``, and the ``marcel_tool`` decorator (a one-release
  deprecation shim — it warns and registers nothing; port to a connector
  habitat, see docs/connectors.md). Resolved
  **lazily** on first attribute access so ``marcel_core`` can import the
  contracts above without an import cycle.

Example — a minimal extension::

    from marcel_sdk import ToolResult, credentials, get_logger

    log = get_logger(__name__)

    def register(marcel):
        @marcel.tool("demo.ping")
        async def ping(params: dict, user_slug: str) -> str:
            log.info("demo.ping for %s", user_slug)
            return "pong"

        @marcel.on("tool_call")
        def audit(event, ctx):
            log.info("%s called %s", ctx.user_slug, event.tool_name)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from marcel_sdk.events import (
    AgentEndEvent,
    BeforeAgentStartEvent,
    BeforeProviderRequestEvent,
    Event,
    EventBus,
    EventContext,
    EventHandler,
    InputEvent,
    ResourcesDiscoverEvent,
    SessionStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from marcel_sdk.extension import ExtensionAPI, ToolHandler, ToolResult

__version__ = '0.1.0'
"""marcel-sdk contract version — independent of the marcel-core kernel
version. Bumped only when this surface changes (with a migration note)."""

# Helpers resolved lazily from marcel_core.plugin — see __getattr__. Kept
# out of the eager import list so `marcel_core` can `from marcel_sdk.events
# import ...` / `from marcel_sdk.extension import ...` without a cycle.
_LAZY_HELPERS = frozenset(
    {'credentials', 'paths', 'models', 'rss', 'get_logger', 'marcel_tool'},
)

if TYPE_CHECKING:  # for type-checkers / docs only; runtime uses __getattr__
    from marcel_core.plugin import (  # noqa: F401
        credentials,
        get_logger,
        marcel_tool,
        models,
        paths,
        rss,
    )


def __getattr__(name: str) -> Any:
    """Lazily resolve kernel-backed helpers from ``marcel_core.plugin``.

    Deferring the import to attribute-access time keeps this package's
    import graph free of ``marcel_core`` so the kernel can depend on the
    SDK contracts without a circular import.
    """
    if name in _LAZY_HELPERS:
        from marcel_core import plugin

        return getattr(plugin, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    'AgentEndEvent',
    'BeforeAgentStartEvent',
    'BeforeProviderRequestEvent',
    'Event',
    'EventBus',
    'EventContext',
    'EventHandler',
    'ExtensionAPI',
    'InputEvent',
    'ResourcesDiscoverEvent',
    'SessionStartEvent',
    'ToolCallEvent',
    'ToolHandler',
    'ToolResult',
    'ToolResultEvent',
    '__version__',
    # lazy (marcel_core-backed) helpers, resolved via __getattr__:
    'credentials',
    'get_logger',
    'marcel_tool',
    'models',
    'paths',
    'rss',
]
