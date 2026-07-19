"""Connector → MCP toolset factory (FEAT-260718-230bf8).

Turns each visible connector into something the agent can actually call:

* **linked** connectors become an :class:`~pydantic_ai.mcp.MCPToolset` pointed at
  the server, wrapped in an ``MCP`` capability carrying the connector's id,
  description and ``tools:`` allowlist. Outbound auth is an ``httpx.Auth`` flow,
  so the header is resolved **per request** from
  :class:`~marcel_core.connectors.auth.ConnectorAuth` — a token refreshed
  mid-conversation is picked up without rebuilding or reconnecting (the
  LibreChat pattern).
* **unlinked** per-user connectors become a deferred, tool-less capability whose
  instructions are the readable "needs setup" message. The model can surface it
  when the user asks, and the connector never presents tools it cannot call —
  the same shape skills use for ``SETUP.md`` (NFR3).

``discovery: deferred`` (the default) defers the capability so its tool schemas
stay out of context until the model reaches for them — via ``ToolSearch`` or via
a skill naming the connector in ``marcel-connectors``. ``eager`` opts a hot-path
connector out.

Only the ``http`` transport is built here; ``stdio``/``inprocess`` need the
per-user process lifecycle and land with that story.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx
from pydantic_ai.capabilities import MCP, AbstractCapability, Capability
from pydantic_ai.mcp import MCPToolset

from marcel_core.connectors.auth import ConnectorAuth
from marcel_core.connectors.loader import ConnectorDoc, load_connectors
from marcel_core.connectors.models import ConnectorConfig, Discovery, Transport

log = logging.getLogger(__name__)


class _PerRequestAuth(httpx.Auth):
    """Resolves the connector's outbound header on every request.

    Keeping this at the httpx layer (rather than baking a header in at build
    time) is what makes a mid-conversation token refresh transparent: each
    request asks :class:`ConnectorAuth` afresh, and ConnectorAuth refreshes
    proactively before expiry.
    """

    def __init__(self, config: ConnectorConfig, slug: str, auth: ConnectorAuth) -> None:
        self._config = config
        self._slug = slug
        self._auth = auth

    async def async_auth_flow(self, request: httpx.Request):
        headers = await self._auth.outbound_headers(self._config, self._slug)
        request.headers.update(headers)
        yield request


def _needs_setup_capability(config: ConnectorConfig, message: str) -> Capability:
    """A deferred, tool-less capability that explains how to connect the account."""
    return Capability(
        id=config.name,
        description=f'{config.description} — needs setup',
        instructions=(
            f'{message}\n\n'
            f'Tell the user plainly that {config.name} is not connected for them yet and what they '
            f'need to do. Do not attempt to call its tools — it has none until it is connected.'
        ),
        defer_loading=True,
    )


def _connector_toolset(config: ConnectorConfig, slug: str, auth: ConnectorAuth) -> MCPToolset:
    """The MCPToolset for one linked http connector."""
    url = config.server.url
    if url is None:  # pragma: no cover - the schema guarantees a url for http
        raise ValueError(f'connector {config.name!r} has http transport without a url')
    return MCPToolset(
        url,
        id=config.name,
        auth=_PerRequestAuth(config, slug, auth),
    )


def build_connector_capabilities(
    user_slug: str,
    role: str = 'user',
    *,
    auth: ConnectorAuth | None = None,
    docs: Sequence[ConnectorDoc] | None = None,
) -> list[AbstractCapability]:
    """The connector capabilities visible to *user_slug* at *role*.

    ``docs`` lets a caller reuse an already-loaded catalog (the composition root
    loads it once for both connectors and skill linking).
    """
    resolver = auth or ConnectorAuth()
    catalog = list(docs) if docs is not None else load_connectors(user_slug, role)
    capabilities: list[AbstractCapability] = []

    for doc in catalog:
        config = doc.config
        if config.server.transport is not Transport.HTTP:
            # stdio / inprocess need the per-user process lifecycle.
            continue
        problem = resolver.linkage_error(config, user_slug)
        if problem is not None:
            capabilities.append(_needs_setup_capability(config, problem))
            continue
        capabilities.append(
            MCP(
                local=_connector_toolset(config, user_slug, resolver),
                id=config.name,
                description=config.description,
                allowed_tools=list(config.tools) or None,
                defer_loading=config.discovery is Discovery.DEFERRED,
            )
        )
    return capabilities


def connector_toolsets_for_skill(
    connector_names: Sequence[str],
    user_slug: str,
    role: str = 'user',
    *,
    auth: ConnectorAuth | None = None,
    docs: Sequence[ConnectorDoc] | None = None,
) -> list[MCPToolset]:
    """Toolsets for the connectors a skill names in ``marcel-connectors``.

    Handing these to the skill's ``Capability(toolsets=…)`` is what makes a
    connector activate together with the skill's ``load_capability`` bundle:
    loading the skill reveals its connectors' tools in the same step. Connectors
    the user cannot see, cannot use, or that are not http are simply omitted —
    the skill still loads, and its SETUP.md path explains the gap.
    """
    if not connector_names:
        return []
    resolver = auth or ConnectorAuth()
    catalog = {d.name: d for d in (docs if docs is not None else load_connectors(user_slug, role))}

    toolsets: list[MCPToolset] = []
    for name in connector_names:
        doc = catalog.get(name)
        if doc is None or doc.config.server.transport is not Transport.HTTP:
            continue
        if resolver.linkage_error(doc.config, user_slug) is not None:
            log.debug('connectors: %s not linked for %s — omitted from skill bundle', name, user_slug)
            continue
        toolsets.append(_connector_toolset(doc.config, user_slug, resolver))
    return toolsets
