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

``http`` connectors are a shared upstream reached with a per-request header.
``stdio``/``inprocess`` connectors instead get one instance per (connector,
user) from :class:`~marcel_core.connectors.lifecycle.ConnectorRegistry`, because
their credential is delivered once in the spawn environment. Building either is
cheap and spawns nothing — fastmcp defers the subprocess to the first connect.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast

import httpx
from pydantic_ai.capabilities import MCP, AbstractCapability, Capability
from pydantic_ai.mcp import MCPToolset

from marcel_core.connectors.auth import ConnectorAuth
from marcel_core.connectors.lifecycle import ConnectorRegistry, ConnectorStartError
from marcel_core.connectors.loader import ConnectorDoc, load_connectors
from marcel_core.connectors.models import ConnectorConfig, Discovery, Transport

log = logging.getLogger(__name__)


def _default_registry() -> ConnectorRegistry:
    """The process-wide registry for spawned connectors.

    Built lazily so importing this module spawns nothing and so tests can
    substitute their own. ``closer`` shuts the toolset down when reaped.
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ConnectorRegistry(
            lambda config, slug: _spawned_toolset(config, slug, ConnectorAuth()),
            closer=_close_toolset,
        )
    return _REGISTRY


_REGISTRY: ConnectorRegistry | None = None


def active_registry() -> ConnectorRegistry | None:
    """The process-wide registry if one was ever built, else ``None``.

    Lets the server's lifespan reap idle instances and shut them down without
    forcing a registry into existence on a Marcel that has no connectors.
    """
    return _REGISTRY


async def _close_toolset(instance: object) -> None:
    closer = getattr(instance, 'aclose', None) or getattr(instance, 'close', None)
    if closer is not None:
        await closer()


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


def _spawned_toolset(config: ConnectorConfig, slug: str, auth: ConnectorAuth) -> MCPToolset:
    """The MCPToolset for one linked stdio/inprocess connector.

    Constructing this spawns nothing — fastmcp defers the subprocess (or the
    in-process server import) to the first connect. The credential is baked into
    the environment here because a spawned server has no per-request header;
    that is also why the instance is keyed per (connector, user) upstream.
    """
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport

    if config.server.transport is Transport.INPROCESS:
        # No credential is resolved here on purpose: the schema pins inprocess to
        # `auth: none`, because an in-process server cannot receive a per-user
        # secret. Identity is different from a credential though — a park that
        # stores per-user *data* (news articles, say) may expose a factory, and
        # the registry's per-(connector, user) keying hands each user their own
        # instance closed over their slug.
        module = config.server.module
        if module is None:  # pragma: no cover - schema guarantees it
            raise ValueError(f'connector {config.name!r} is inprocess without a module')
        return MCPToolset(_load_inprocess_server(module, slug, getattr(config, '_connector_dir', None)), id=config.name)

    env = auth.spawn_env(config, slug)
    command = config.server.command
    if not command:  # pragma: no cover - schema guarantees it
        raise ValueError(f'connector {config.name!r} is stdio without a command')
    transport = StdioTransport(command=command[0], args=list(command[1:]), env=env)
    return MCPToolset(Client(transport), id=config.name)


def _load_inprocess_server(module_path: str, slug: str | None = None, connector_dir: Any = None) -> Any:
    """Import a bundled in-process FastMCP server by dotted path or park file.

    The habitat's ``server.module`` exposes one of two shapes:

    * ``build(user_slug) -> FastMCP`` — a **factory**, for parks whose data is
      per-user. The registry keys instances per (connector, user), so each user
      gets their own server closed over their slug. The slug is identity, not a
      credential — ``auth: none`` still holds.
    * ``mcp`` — a module-level FastMCP **singleton**, for genuinely
      user-agnostic servers. Preferred only when there is no per-user state.

    In-process servers share Marcel's own process, so this import is trusted
    code by construction (see :mod:`marcel_core.connectors.lifecycle` on the
    trust model).
    """
    import importlib

    if module_path.endswith('.py'):
        # A park-relative file (D3, FEAT-260718-c232d9): zoo connectors are not
        # importable packages, so `server.module: server.py` loads from the
        # habitat directory — the same spec_from_file_location shape the
        # extension loader uses.
        import importlib.util
        from pathlib import Path

        if connector_dir is None:
            raise ValueError(f'{module_path!r} is park-relative but the connector directory is unknown')
        target = Path(str(connector_dir)) / module_path
        if not target.is_file():
            raise ValueError(f'connector server file {target} does not exist')
        spec = importlib.util.spec_from_file_location(f'marcel_connector_servers.{target.parent.name}', target)
        if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
            raise ValueError(f'cannot load connector server from {target}')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_path)
    build = getattr(module, 'build', None)
    if callable(build) and slug is not None:
        return build(slug)
    server = getattr(module, 'mcp', None)
    if server is None:
        raise ValueError(f'{module_path!r} exposes neither a `build(user_slug)` factory nor an `mcp` server')
    return server


def build_connector_capabilities(
    user_slug: str,
    role: str = 'user',
    *,
    auth: ConnectorAuth | None = None,
    docs: Sequence[ConnectorDoc] | None = None,
    registry: ConnectorRegistry | None = None,
) -> list[AbstractCapability]:
    """The connector capabilities visible to *user_slug* at *role*.

    ``docs`` lets a caller reuse an already-loaded catalog (the composition root
    loads it once for both connectors and skill linking).
    """
    resolver = auth or ConnectorAuth()
    catalog = list(docs) if docs is not None else load_connectors(user_slug, role)
    capabilities: list[AbstractCapability] = []

    reg = registry if registry is not None else _default_registry()

    for doc in catalog:
        config = doc.config
        spawned = ConnectorRegistry.handles(config)
        try:
            problem = resolver.linkage_error(config, user_slug, for_spawn=spawned)
            if problem is not None:
                capabilities.append(_needs_setup_capability(config, problem))
                continue
            toolset = (
                cast(MCPToolset, reg.acquire(config, user_slug))
                if spawned
                else _connector_toolset(config, user_slug, resolver)
            )
        except ConnectorStartError as exc:
            # Backoff or a failed build — surface it readably rather than
            # advertising tools that cannot run.
            capabilities.append(_needs_setup_capability(config, str(exc)))
            continue
        except (ValueError, OSError) as exc:
            # One malformed habitat must never break the agent build for every
            # family member (FR1's isolation contract). Degrade this connector,
            # keep the rest of the catalog.
            log.warning('connectors: skipping malformed %s — %s', config.name, type(exc).__name__)
            capabilities.append(_needs_setup_capability(config, 'This connector is misconfigured.'))
            continue
        capabilities.append(
            MCP(
                local=toolset,
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
    registry: ConnectorRegistry | None = None,
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

    reg = registry if registry is not None else _default_registry()
    toolsets: list[MCPToolset] = []
    for name in connector_names:
        doc = catalog.get(name)
        if doc is None:
            continue
        config = doc.config
        spawned = ConnectorRegistry.handles(config)
        try:
            if resolver.linkage_error(config, user_slug, for_spawn=spawned) is not None:
                log.debug('connectors: %s not linked for %s — omitted from skill bundle', name, user_slug)
                continue
            toolset = (
                cast(MCPToolset, reg.acquire(config, user_slug))
                if spawned
                else _connector_toolset(config, user_slug, resolver)
            )
        except (ConnectorStartError, ValueError, OSError):
            log.debug('connectors: %s unusable for %s — omitted from skill bundle', name, user_slug)
            continue
        toolsets.append(toolset)
    return toolsets


async def call_connector_tool(ref: str, params: dict, slug: str, *, timeout: float = 300.0) -> str:
    """Call ``<connector>.<tool>`` directly — the job-dispatch path (D2).

    ``dispatch_type: tool`` job refs like ``news.sync`` historically named a
    toolkit handler. During and after the toolkit→connector migration
    (FEAT-260718-c232d9) the same ref resolves here when no toolkit claims it:
    connector ``news``, MCP tool ``sync``, invoked over the real protocol via a
    fastmcp Client. Job templates stay unchanged across the migration.

    System-scope jobs pass their run slug through (``_system`` fans out inside
    the park's own tool, exactly as the toolkit handler did).
    """
    import asyncio

    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport

    from marcel_core.connectors.loader import get_connector

    family, _, tool = ref.partition('.')
    if not tool:
        raise KeyError(f'{ref!r} is not a <connector>.<tool> reference')
    doc = get_connector(family, None, role='admin')
    if doc is None:
        raise KeyError(f'no connector named {family!r}')
    config = doc.config

    if config.server.transport is Transport.INPROCESS:
        server = _load_inprocess_server(config.server.module or '', slug, doc.connector_dir)
        client = Client(server)
    elif config.server.transport is Transport.STDIO:
        env = ConnectorAuth().spawn_env(config, slug)
        command = config.server.command or []
        client = Client(StdioTransport(command=command[0], args=list(command[1:]), env=env))
    else:
        raise KeyError(f'connector {family!r} is http — schedule against its own service instead')

    async with client:
        result = await asyncio.wait_for(client.call_tool(tool, params or {}), timeout=timeout)
    parts = getattr(result, 'content', None) or []
    return '\n'.join(str(getattr(part, 'text', part)) for part in parts)
