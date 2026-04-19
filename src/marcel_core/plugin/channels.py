"""Channel plugin surface — stable API for channel habitats.

Channel habitats self-register with the kernel so `main.py` can mount routers
and the prompt builder can query capabilities without importing transport-
specific modules directly. The goal is that kernel code depends on the
abstraction in this module, not on `marcel_core.channels.telegram.*` (or any
other concrete transport).

Current shape:

- :class:`ChannelPlugin` Protocol — identity (`name`, `capabilities`,
  `router`) plus three push-delivery methods (`send_message`,
  `send_photo`, `send_artifact_link`). Unsupported shapes return
  ``False``; the protocol carries no opinion on which channels implement
  which shapes — capability flags on :class:`ChannelCapabilities` are the
  declared truth.
- :func:`register_channel`, :func:`get_channel`, :func:`list_channels` —
  registry management.
- :func:`channel_has_rich_ui` — registry-backed capability query used by
  :func:`marcel_core.channels.adapter.channel_supports_rich_ui` as its
  authoritative source before falling back to the kernel built-in set.

Stage 3 of ISSUE-7d6b3f will add `resolve_user_slug(request)` for the API
pull sites.

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

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from marcel_core.channels.adapter import ChannelCapabilities

if TYPE_CHECKING:
    from fastapi import APIRouter

log = logging.getLogger(__name__)


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
