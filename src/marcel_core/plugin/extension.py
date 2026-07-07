"""Concrete ``register(marcel)`` extension API + loader (F0).

An extension is a module exposing ``def register(marcel)`` where ``marcel``
is the object defined here (implementing the :class:`marcel_sdk.ExtensionAPI`
protocol). Running the function *is* the registration — no base class, no
manifest (ADR-260628-9f4c41).

Registrations route to where the kernel already looks:

- ``marcel.tool(name)`` → the toolkit registry (same as ``@marcel_tool``),
  so extension tools are dispatchable immediately via the ``toolkit`` tool.
- ``marcel.channel(plugin)`` → the channel registry (``register_channel``).
- ``marcel.on(event, handler)`` → the :class:`ExtensionRegistry`, replayed
  onto **each turn's** event bus by
  :func:`~marcel_core.harness.runner.stream_turn` (the bus is per-turn).
- ``marcel.skill / job / agent / command`` → recorded on the registry.
  Full loader integration for these lands in F1 when the habitats migrate;
  F0 establishes the entrypoint and keeps the records retrievable.

This loader coexists with the five per-kind habitat loaders through the
F0 → F1 migration; it does not replace them yet.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import cast

from marcel_sdk.events import (
    EventBus,
    EventContext,
    EventHandler,
    ResourcesDiscoverEvent,
)
from marcel_sdk.extension import ToolHandler

log = logging.getLogger(__name__)

_EXTENSION_MODULE_PREFIX = '_marcel_ext'


@dataclass
class ExtensionRegistry:
    """Collects capabilities registered by extensions via the ``marcel`` object.

    Tools and channels are pushed into their existing kernel registries at
    registration time (so they work immediately); event handlers and the
    remaining kinds are collected here. Event handlers are replayed onto
    every turn's :class:`~marcel_sdk.events.EventBus` via
    :meth:`apply_to_bus`.
    """

    handlers: list[tuple[str, EventHandler]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    commands: dict[str, Callable[..., Awaitable[None]]] = field(default_factory=dict)
    # Paths contributed by extensions via the resources_discover event (see
    # emit_resources_discover). Collected in F0; the skill/prompt loaders
    # consume them in F1.
    discovered_skill_paths: list[str] = field(default_factory=list)
    discovered_prompt_paths: list[str] = field(default_factory=list)

    def apply_to_bus(self, bus: EventBus) -> None:
        """Subscribe every extension-registered handler onto *bus*."""
        for name, handler in self.handlers:
            bus.on(name, handler)

    def clear(self) -> None:
        """Drop all collected registrations (used when reloading / in tests)."""
        self.handlers.clear()
        self.skills.clear()
        self.jobs.clear()
        self.agents.clear()
        self.commands.clear()
        self.discovered_skill_paths.clear()
        self.discovered_prompt_paths.clear()


# Process-wide registry populated at startup by load_extensions().
_REGISTRY = ExtensionRegistry()


def extension_registry() -> ExtensionRegistry:
    """Return the process-wide extension registry."""
    return _REGISTRY


class MarcelExtensionAPI:
    """The concrete ``marcel`` object handed to ``register(marcel)``.

    Implements the :class:`marcel_sdk.ExtensionAPI` protocol structurally.
    """

    def __init__(self, registry: ExtensionRegistry) -> None:
        self._registry = registry

    def tool(self, name: str) -> Callable[[ToolHandler], ToolHandler]:
        """Register a toolkit handler under ``name`` (``"family.action"``).

        Delegates to the same registry the ``@marcel_tool`` decorator uses,
        so ``@marcel.tool("x.y")`` and ``@marcel_tool("x.y")`` are equivalent
        entrypoints — ``@marcel_tool`` is the back-compat sugar.

        ``marcel_tool`` is typed for ``str``-returning handlers; the SDK
        ``ToolHandler`` also permits a ``ToolResult`` return. F0 handlers
        return ``str`` (ToolResult dispatch handling is future work), so the
        decorator is bridged to the SDK signature with a cast.
        """
        from marcel_core.toolkit import marcel_tool

        return cast('Callable[[ToolHandler], ToolHandler]', marcel_tool(name))

    def on(self, event: str, handler: EventHandler) -> None:
        """Subscribe ``handler`` to a lifecycle event on every turn's bus."""
        self._registry.handlers.append((event, handler))

    def channel(self, plugin: object) -> None:
        """Register a channel plugin (transport + formatting adapter)."""
        from marcel_core.plugin.channels import register_channel

        register_channel(plugin)  # type: ignore[arg-type]

    def skill(self, source: str) -> None:
        """Register a skill habitat by its ``SKILL.md`` directory path."""
        self._registry.skills.append(source)

    def job(self, source: str) -> None:
        """Register a job template by its ``template.yaml`` directory path."""
        self._registry.jobs.append(source)

    def agent(self, source: str) -> None:
        """Register a subagent by its Markdown definition path."""
        self._registry.agents.append(source)

    def command(self, name: str, handler: Callable[..., Awaitable[None]]) -> None:
        """Register a platform/slash command handler."""
        self._registry.commands[name] = handler


def _load_extension_module(entry: Path) -> ModuleType | None:
    """Import a single extension entry (a package dir or a ``.py`` file)."""
    if entry.is_dir():
        init = entry / '__init__.py'
        if not init.is_file():
            return None
        target, modname, search = init, f'{_EXTENSION_MODULE_PREFIX}.{entry.name}', [str(entry)]
    elif entry.suffix == '.py' and not entry.name.startswith(('_', '.')):
        target, modname, search = entry, f'{_EXTENSION_MODULE_PREFIX}.{entry.stem}', [str(entry.parent)]
    else:
        return None

    if modname in sys.modules:
        return sys.modules[modname]

    spec = importlib.util.spec_from_file_location(modname, target, submodule_search_locations=search)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        log.exception('extension %s failed to import', entry.name)
        sys.modules.pop(modname, None)
        return None
    return module


def load_extensions(
    zoo_dir: Path | None,
    registry: ExtensionRegistry | None = None,
) -> list[str]:
    """Discover and run ``register(marcel)`` extensions under ``<zoo>/extensions/``.

    Each entry is a module — a directory with ``__init__.py`` or a ``.py``
    file — exposing ``def register(marcel)``. Discovery is idempotent per
    module (``sys.modules``-guarded); a broken extension is logged and
    skipped, never fatal.

    Returns the names of the extensions whose ``register`` ran successfully.
    """
    reg = registry if registry is not None else _REGISTRY
    if zoo_dir is None:
        return []
    ext_dir = zoo_dir / 'extensions'
    if not ext_dir.is_dir():
        return []

    api = MarcelExtensionAPI(reg)
    loaded: list[str] = []
    for entry in sorted(ext_dir.iterdir()):
        if entry.name.startswith(('_', '.')):
            continue
        module = _load_extension_module(entry)
        if module is None:
            continue
        register_fn = getattr(module, 'register', None)
        if not callable(register_fn):
            log.warning('extension %r has no register(marcel) — skipping', entry.name)
            continue
        # Clean name: the module stem, so a dir ``foo/`` and a file
        # ``foo.py`` both report as ``foo`` (matches the module namespace).
        name = entry.stem
        try:
            register_fn(api)
        except Exception:
            log.exception('extension %r register(marcel) failed', name)
            continue
        loaded.append(name)
        log.info('extension %r registered', name)
    return loaded


async def emit_resources_discover(
    registry: ExtensionRegistry | None = None,
    *,
    reason: str = 'startup',
) -> ResourcesDiscoverEvent:
    """Fire the ``resources_discover`` event so extensions contribute paths.

    Emitted once at startup (from ``main.lifespan``) after extensions are
    loaded. Extension ``on("resources_discover")`` handlers append skill /
    prompt paths to the event; the contributions are collected onto the
    registry (``discovered_skill_paths`` / ``discovered_prompt_paths``). The
    skill and prompt loaders consume them in F1 — F0 establishes the hook
    and the collection. Returns the event for inspection/tests.
    """
    reg = registry if registry is not None else _REGISTRY
    bus = EventBus()
    reg.apply_to_bus(bus)
    event = await bus.emit(
        ResourcesDiscoverEvent(reason=reason),
        EventContext(user_slug='', role=''),
    )
    reg.discovered_skill_paths.extend(event.skill_paths)
    reg.discovered_prompt_paths.extend(event.prompt_paths)
    return event
