"""The extension contract: ``register(marcel)`` and the ``marcel`` object.

A Marcel extension is a module exposing ``def register(marcel: ExtensionAPI)
-> None``; running the function *is* the registration. Capabilities are
registered as side effects on the ``marcel`` object — there is no base
class and no parallel manifest to keep in sync (ADR-260628-9f4c41).

This module defines the *contract* (the :class:`ExtensionAPI` protocol,
:class:`ToolResult`, and the handler type). The concrete object handed to
``register`` is built by the kernel's loader; extensions type-hint against
this protocol and never import ``marcel_core`` internals.

Dependency-free by design (no ``marcel_core`` import) — see
:mod:`marcel_sdk.events`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from marcel_sdk.events import EventHandler


@dataclass
class ToolResult:
    """Structured result a tool handler may return instead of a bare ``str``.

    The historical handler contract returns ``str``; that keeps working.
    A handler that needs to signal an error (so the ``tool_result`` event
    and the model both see ``is_error``) returns a :class:`ToolResult`
    instead. Room to grow (structured details, renderer hints) without
    breaking the string path.
    """

    text: str
    is_error: bool = False


# The historical toolkit-handler shape: ``(params, user_slug) -> str |
# ToolResult``, async. Kept only so :meth:`ExtensionAPI.tool` — the retired
# registration path — stays typeable during its one-release deprecation
# window (FEAT-260718-c232d9).
ToolHandler = Callable[[dict, str], Awaitable['str | ToolResult']]


@runtime_checkable
class ExtensionAPI(Protocol):
    """The ``marcel`` object handed to ``register(marcel)``.

    Every capability an extension can add is a method here. The five
    habitat kinds (skill / connector / channel / job / subagent) become
    *things you register* through one object, plus ``on`` to subscribe to
    the lifecycle event bus.

    This is a :class:`typing.Protocol`: the kernel provides the concrete
    implementation, extensions type against the protocol. It is the
    versioned surface — adding a method is compatible; changing an
    existing signature is a breaking SDK change requiring a version bump.
    """

    def tool(self, name: str) -> Callable[[ToolHandler], ToolHandler]:
        """Deprecated no-op — the toolkit habitat retired (FEAT-260718-c232d9).

        The decorator emits a :class:`DeprecationWarning` and registers
        nothing. Port the extension to :meth:`connector` — an MCP server
        carries the tools now (see docs/connectors.md). Removed next release.
        """
        ...

    def on(self, event: str, handler: EventHandler) -> None:
        """Subscribe ``handler`` to a lifecycle event (see :mod:`marcel_sdk.events`)."""
        ...

    def channel(self, plugin: object) -> None:
        """Register a channel plugin (transport + formatting adapter)."""
        ...

    def skill(self, source: str) -> None:
        """Register a skill habitat by its ``SKILL.md`` directory path."""
        ...

    def connector(self, source: str) -> None:
        """Register a connector habitat by its ``connector.yaml`` directory path.

        A connector is an MCP server plus its per-user authentication
        (FEAT-260718-230bf8). Note that connector code runs as trusted code —
        see the Trust model in the connectors documentation.
        """
        ...

    def job(self, source: str) -> None:
        """Register a job template by its ``template.yaml`` directory path."""
        ...

    def agent(self, source: str) -> None:
        """Register a subagent by its Markdown definition path."""
        ...

    def command(self, name: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Register a platform/slash command handler."""
        ...
