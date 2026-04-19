"""Channel plugin surface — stable API for channel habitats.

Channel habitats self-register with the kernel so `main.py` can mount routers
and the prompt builder can query capabilities without importing transport-
specific modules directly. The goal is that kernel code depends on the
abstraction in this module, not on `marcel_core.channels.telegram.*` (or any
other concrete transport).

Current shape:

- :class:`ChannelPlugin` Protocol — identity (`name`, `capabilities`,
  `router`) plus push-delivery methods (`send_message`, `send_photo`,
  `send_artifact_link`) and pull-side `resolve_user_slug`. Unsupported
  shapes return ``False``/``None``; capability flags on
  :class:`ChannelCapabilities` are the declared truth.
- :func:`register_channel`, :func:`get_channel`, :func:`list_channels` —
  registry management.
- :func:`channel_has_rich_ui` — registry-backed capability query used by
  :func:`marcel_core.channels.adapter.channel_supports_rich_ui` as its
  authoritative source before falling back to the kernel built-in set.
- :func:`discover` — walks ``<MARCEL_ZOO_DIR>/channels/`` and imports each
  habitat so its ``__init__.py`` can call :func:`register_channel`.

Example — minimal channel habitat at
``<MARCEL_ZOO_DIR>/channels/demo/__init__.py``::

    from fastapi import APIRouter

    from marcel_core.channels.adapter import ChannelCapabilities
    from marcel_core.plugin import register_channel

    router = APIRouter(prefix='/channels/demo')

    class DemoChannel:
        name = 'demo'
        capabilities = ChannelCapabilities(rich_ui=False)
        router = router

    register_channel(DemoChannel())
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from marcel_core.channels.adapter import ChannelCapabilities

if TYPE_CHECKING:
    from fastapi import APIRouter

log = logging.getLogger(__name__)

# Prefix used for sys.modules entries of dynamically-loaded external channel
# habitats. Kept private so it cannot collide with a future real top-level
# package. Mirrors the convention used by the integration loader.
_EXTERNAL_MODULE_PREFIX = '_marcel_ext_channels'


@runtime_checkable
class ChannelPlugin(Protocol):
    """Contract every channel habitat satisfies.

    Attributes are duck-typed — a plugin is anything exposing the right
    names. A plugin may be a module, a class instance, or a (frozen)
    dataclass; the registry does not care as long as the attributes resolve.
    Attributes are declared as read-only properties so both mutable and
    immutable implementations are accepted.
    """

    @property
    def name(self) -> str:
        """Unique channel identifier. Must match the habitat directory name."""
        ...

    @property
    def capabilities(self) -> ChannelCapabilities:
        """Declares what the channel supports (markdown, rich UI, streaming, …)."""
        ...

    @property
    def router(self) -> APIRouter | None:
        """Optional FastAPI router. Kernel-internal channels (e.g. the
        WebSocket transport) may expose ``None`` if their routing lives
        elsewhere.
        """
        ...

    async def send_message(self, user_slug: str, text: str) -> bool:
        """Deliver a short text message to *user_slug* on this channel.

        *text* is markdown; the channel is responsible for any escaping or
        format translation (e.g. telegram renders markdown as HTML). Returns
        ``True`` if the message was delivered, ``False`` if the channel has
        no recipient registered for *user_slug* (e.g. unlinked telegram
        session). Transport errors propagate.
        """
        ...

    async def send_photo(
        self,
        user_slug: str,
        image_bytes: bytes,
        caption: str | None = None,
    ) -> bool:
        """Deliver a binary image to *user_slug*.

        Only channels with ``capabilities.attachments`` declare meaningful
        support. Returns ``True`` on successful delivery, ``False`` if the
        shape is unsupported or the recipient cannot be resolved.
        """
        ...

    async def send_artifact_link(
        self,
        user_slug: str,
        artifact_id: str,
        title: str,
    ) -> bool:
        """Deliver a link or button that opens *artifact_id* in a rich-UI
        surface.

        Only channels with ``capabilities.rich_ui`` declare meaningful
        support (e.g. the telegram Mini App button). Returns ``True`` on
        delivery, ``False`` if unsupported or the artifact cannot be linked
        (e.g. no public URL configured).
        """
        ...

    def resolve_user_slug(self, external_id: str) -> str | None:
        """Map a channel-side identity (*external_id*) to the marcel user
        slug that owns it, or ``None`` if unlinked.

        For telegram, *external_id* is the ``tg_user['id']`` stringified.
        Channels that do not carry a separate identity space (e.g. the
        plain WebSocket channel, which authenticates via API token) return
        ``None``.
        """
        ...


_registry: dict[str, ChannelPlugin] = {}


def register_channel(plugin: ChannelPlugin) -> None:
    """Add *plugin* to the channel registry.

    Re-registering the same name is allowed (last write wins) and logs a
    warning if the plugin instance changes — this protects against accidental
    double-import during reloads while still surfacing real conflicts.
    """
    existing = _registry.get(plugin.name)
    if existing is not None and existing is not plugin:
        log.warning(
            'Channel %r already registered by %r; replacing with %r',
            plugin.name,
            existing,
            plugin,
        )
    _registry[plugin.name] = plugin


def get_channel(name: str) -> ChannelPlugin | None:
    """Return the registered plugin for *name*, or ``None`` if unregistered."""
    return _registry.get(name)


def list_channels() -> list[str]:
    """Return the names of all registered channels, sorted for determinism."""
    return sorted(_registry)


def channel_has_rich_ui(name: str) -> bool | None:
    """Return the registered ``rich_ui`` capability for *name*.

    Returns ``None`` when *name* is not registered, so the caller can fall
    back to a built-in set. This three-valued return is intentional —
    distinguishing "registered as False" from "unknown channel" matters for
    the adapter-layer fallback logic.
    """
    plugin = _registry.get(name)
    if plugin is None:
        return None
    return plugin.capabilities.rich_ui


def discover() -> None:
    """Discover external channel habitats from ``<MARCEL_ZOO_DIR>/channels/``.

    Each subdirectory is loaded as a Python package; its ``__init__.py`` is
    expected to call :func:`register_channel` at import time. Errors in one
    habitat never abort discovery of its siblings — the failure is logged,
    the habitat is skipped, and the rest load normally.

    Returns silently when ``MARCEL_ZOO_DIR`` is unset or ``<zoo>/channels/``
    does not exist. Safe to call multiple times: already-imported habitats
    are skipped via ``sys.modules``.
    """
    try:
        from marcel_core.config import settings

        zoo_dir = settings.zoo_dir
    except Exception:
        log.exception('Failed to resolve zoo_dir for channel discovery')
        return

    if zoo_dir is None:
        return

    external_dir = zoo_dir / 'channels'
    if not external_dir.is_dir():
        return

    for entry in sorted(external_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(('_', '.')):
            continue
        _load_external_channel(entry)


def _load_external_channel(pkg_dir: Path) -> None:
    """Load one external channel habitat from *pkg_dir*.

    The habitat's ``__init__.py`` is expected to call
    :func:`register_channel` at import time. Failures are logged and
    contained; the caller continues with the next habitat regardless of
    what happens here.
    """
    init_py = pkg_dir / '__init__.py'
    if not init_py.exists():
        log.warning(
            "Channel habitat '%s' has no __init__.py — skipping",
            pkg_dir.name,
        )
        return

    module_name = f'{_EXTERNAL_MODULE_PREFIX}.{pkg_dir.name}'
    if module_name in sys.modules:
        return

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[str(pkg_dir)],
        )
        if spec is None or spec.loader is None:
            log.error(
                "Could not create module spec for channel habitat '%s'",
                pkg_dir.name,
            )
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        log.exception(
            "Failed to load channel habitat '%s'",
            pkg_dir.name,
        )
