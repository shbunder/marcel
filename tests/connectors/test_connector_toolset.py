"""Tests for the connector → MCP toolset factory (story d).

Covers capability shape (id/description/allowlist), deferred vs eager
discovery, the "needs setup" stand-in for unlinked users, the per-request auth
flow, and skill→connector bundling via marcel-connectors.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic_ai.capabilities import Capability

from marcel_core.connectors.auth import ConnectorAuth
from marcel_core.connectors.loader import ConnectorDoc
from marcel_core.connectors.models import ConnectorConfig
from marcel_core.connectors.tokens import StoredTokens, TokenStore
from marcel_core.connectors.toolset import (
    _PerRequestAuth,
    build_connector_capabilities,
    connector_toolsets_for_skill,
)
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(settings, 'marcel_credentials_key', 'test-passphrase-123')


def _cfg(name='weather', *, mode='api_key', discovery='deferred', tools=(), transport='http', scope='all'):
    server = {'transport': 'http', 'url': 'https://mcp.test/sse'}
    if transport == 'stdio':
        server = {'transport': 'stdio', 'command': ['x']}
    auth: dict = {'mode': 'api_key', 'per_user': True, 'credential_keys': ['WEATHER_API_KEY']}
    if mode == 'none':
        auth = {'mode': 'none', 'per_user': False}
    if mode == 'oauth':
        auth = {'mode': 'oauth', 'oauth': {'issuer': 'https://i.test', 'client_id': 'app'}}
    return ConnectorConfig.model_validate(
        {
            'name': name,
            'description': f'{name} connector',
            'server': server,
            'auth': auth,
            'tools': list(tools),
            'discovery': discovery,
            'scope': scope,
        }
    )


def _doc(cfg, tmp_path=None):
    from pathlib import Path

    return ConnectorDoc(config=cfg, source='zoo-global', connector_dir=Path(tmp_path or '/x'))


def _fake_toolset(name='clock'):
    """A real MCPToolset over the bundled fake server — what a registry hands back."""
    from pydantic_ai.mcp import MCPToolset

    from marcel_core.connectors.toolset import _load_inprocess_server

    return MCPToolset(_load_inprocess_server('tests.connectors.fake_inprocess_server'), id=name)


def _link_api_key(slug='shaun'):
    from marcel_core.storage.credentials import save_credentials

    save_credentials(slug, {'WEATHER_API_KEY': 'user-key'})


class TestBuildConnectorCapabilities:
    def test_linked_connector_becomes_mcp_capability(self):
        _link_api_key()
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg())])
        assert len(caps) == 1
        assert caps[0].id == 'weather'
        assert caps[0].defer_loading is True  # discovery: deferred (default)

    def test_eager_discovery_is_not_deferred(self):
        _link_api_key()
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(discovery='eager'))])
        assert caps[0].defer_loading is False

    def test_unlinked_becomes_needs_setup_capability(self):
        # No credential saved for this user.
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg())])
        assert len(caps) == 1
        cap = caps[0]
        assert cap.id == 'weather'
        assert (cap.description or '').endswith('— needs setup')
        assert cap.defer_loading is True
        # Its instructions carry the readable reason and it exposes no tools.
        instructions = str(cap.get_instructions())
        assert 'WEATHER_API_KEY' in instructions
        assert not getattr(cap, 'tools', None)

    def test_per_user_isolation(self):
        """User A linked, user B not — B gets the needs-setup stand-in."""
        _link_api_key('alice')
        docs = [_doc(_cfg())]
        a = build_connector_capabilities('alice', docs=docs)[0]
        b = build_connector_capabilities('bob', docs=docs)[0]
        assert not (a.description or '').endswith('— needs setup')
        assert (b.description or '').endswith('— needs setup')

    def test_auth_none_needs_no_link(self):
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(mode='none'))])
        assert not (caps[0].description or '').endswith('— needs setup')

    def test_oauth_unlinked_is_readable(self):
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(name='gh', mode='oauth'))])
        instructions = str(caps[0].get_instructions())
        assert 'not linked' in instructions

    def test_oauth_linked_builds_toolset(self):
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='a'))
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(name='gh', mode='oauth'))])
        assert not (caps[0].description or '').endswith('— needs setup')

    def test_spawned_transport_is_handled_not_skipped(self):
        """stdio/inprocess are built via the registry (story e), not dropped."""
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        _link_api_key()
        reg = ConnectorRegistry(lambda c, s: _fake_toolset(c.name))
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(transport='stdio', mode='none'))], registry=reg)
        assert [c.id for c in caps] == ['weather']

    def test_empty_catalog(self):
        assert build_connector_capabilities('shaun', docs=[]) == []


class TestPerRequestAuth:
    @pytest.mark.asyncio
    async def test_header_resolved_at_request_time(self):
        """The header is read per request, so a refreshed token is picked up."""
        _link_api_key()
        cfg = _cfg()
        flow = _PerRequestAuth(cfg, 'shaun', ConnectorAuth())
        request = httpx.Request('GET', 'https://mcp.test/sse')
        gen = flow.async_auth_flow(request)
        prepared = await gen.__anext__()
        assert prepared.headers['Authorization'] == 'Bearer user-key'
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_reflects_rotated_credential_without_rebuild(self):
        from marcel_core.storage.credentials import save_credentials

        _link_api_key()
        flow = _PerRequestAuth(_cfg(), 'shaun', ConnectorAuth())

        async def header():
            gen = flow.async_auth_flow(httpx.Request('GET', 'https://mcp.test/sse'))
            req = await gen.__anext__()
            await gen.aclose()
            return req.headers['Authorization']

        assert await header() == 'Bearer user-key'
        save_credentials('shaun', {'WEATHER_API_KEY': 'rotated-key'})
        # Same auth object, no rebuild — the new value is used.
        assert await header() == 'Bearer rotated-key'


class TestSkillConnectorBundling:
    def test_linked_connector_toolset_returned(self):
        _link_api_key()
        toolsets = connector_toolsets_for_skill(['weather'], 'shaun', docs=[_doc(_cfg())])
        assert len(toolsets) == 1

    def test_unlinked_connector_omitted(self):
        toolsets = connector_toolsets_for_skill(['weather'], 'shaun', docs=[_doc(_cfg())])
        assert toolsets == []

    def test_unknown_connector_omitted(self):
        _link_api_key()
        assert connector_toolsets_for_skill(['ghost'], 'shaun', docs=[_doc(_cfg())]) == []

    def test_spawned_connector_bundled_too(self):
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        reg = ConnectorRegistry(lambda c, s: _fake_toolset(c.name))
        cfg = _cfg(transport='stdio', mode='none')
        assert len(connector_toolsets_for_skill(['weather'], 'shaun', docs=[_doc(cfg)], registry=reg)) == 1

    def test_no_names_is_empty(self):
        assert connector_toolsets_for_skill([], 'shaun', docs=[_doc(_cfg())]) == []

    def test_skill_capability_carries_connector_toolsets(self, tmp_path, monkeypatch):
        """A skill naming marcel-connectors gets those toolsets on its capability,
        so loading the skill activates the connector in the same step."""
        from marcel_core.config import settings
        from marcel_core.skills.capability import build_skill_capabilities

        zoo = tmp_path / 'zoo'
        sdir = zoo / 'skills' / 'forecast'
        sdir.mkdir(parents=True)
        (sdir / 'SKILL.md').write_text(
            '---\nname: forecast\ndescription: Weather talk\n'
            'metadata:\n  marcel-connectors: weather\n---\n\nHow to forecast.'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
        _link_api_key()

        caps = build_skill_capabilities('shaun', connector_docs=[_doc(_cfg())])
        forecast = next(c for c in caps if c.id == 'forecast')
        assert forecast.toolsets  # the connector rides along with the skill

    def test_skill_without_connector_docs_has_no_toolsets(self, tmp_path, monkeypatch):
        from marcel_core.config import settings
        from marcel_core.skills.capability import build_skill_capabilities

        zoo = tmp_path / 'zoo'
        sdir = zoo / 'skills' / 'forecast'
        sdir.mkdir(parents=True)
        (sdir / 'SKILL.md').write_text(
            '---\nname: forecast\ndescription: Weather talk\n'
            'metadata:\n  marcel-connectors: weather\n---\n\nHow to forecast.'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        caps = build_skill_capabilities('shaun', connector_docs=None)
        forecast = next(c for c in caps if c.id == 'forecast')
        assert not forecast.toolsets


class TestCompositionWiring:
    def test_connectors_off_yields_no_connector_caps(self, tmp_path, monkeypatch):
        from marcel_core.composition import build_capabilities

        caps = build_capabilities(user_slug='shaun', connectors=False, skills=False)
        assert not any(isinstance(c, Capability) and c.id == 'weather' for c in caps)

    def test_toolsearch_added_when_a_deferred_connector_exists(self, tmp_path, monkeypatch):
        from pydantic_ai.capabilities import ToolSearch

        from marcel_core.composition import build_capabilities
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        cdir = zoo / 'connectors' / 'weather'
        cdir.mkdir(parents=True)
        (cdir / 'connector.yaml').write_text(
            'name: weather\ndescription: Weather\n'
            'server: {transport: http, url: https://mcp.test}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        caps = build_capabilities(user_slug='shaun', skills=False)
        assert any(isinstance(c, ToolSearch) for c in caps)

    def test_no_toolsearch_when_all_eager(self, tmp_path, monkeypatch):
        from pydantic_ai.capabilities import ToolSearch

        from marcel_core.composition import build_capabilities
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        cdir = zoo / 'connectors' / 'weather'
        cdir.mkdir(parents=True)
        (cdir / 'connector.yaml').write_text(
            'name: weather\ndescription: Weather\ndiscovery: eager\n'
            'server: {transport: http, url: https://mcp.test}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        caps = build_capabilities(user_slug='shaun', skills=False)
        assert not any(isinstance(c, ToolSearch) for c in caps)

    def test_admin_scoped_connector_hidden_from_user(self, tmp_path, monkeypatch):
        from marcel_core.composition import build_capabilities
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        cdir = zoo / 'connectors' / 'secret'
        cdir.mkdir(parents=True)
        (cdir / 'connector.yaml').write_text(
            'name: secret\ndescription: Admin only\nscope: admin\n'
            'server: {transport: http, url: https://mcp.test}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        user_caps = build_capabilities(user_slug='shaun', role='user', skills=False)
        admin_caps = build_capabilities(user_slug='shaun', role='admin', skills=False)
        assert not any(getattr(c, 'id', None) == 'secret' for c in user_caps)
        assert any(getattr(c, 'id', None) == 'secret' for c in admin_caps)


# ---------------------------------------------------------------------------
# spawned transports (stdio / inprocess) — story e
# ---------------------------------------------------------------------------


def _spawn_cfg(name='clock', transport='stdio', mode='none'):
    server = {'transport': 'stdio', 'command': ['clock-mcp', '--stdio']}
    if transport == 'inprocess':
        server = {'transport': 'inprocess', 'module': 'tests.connectors.fake_inprocess_server'}
    auth: dict = {'mode': 'none', 'per_user': False}
    if mode == 'api_key':
        auth = {'mode': 'api_key', 'per_user': True, 'credential_keys': ['CLOCK_API_KEY']}
    if mode == 'oauth':
        auth = {'mode': 'oauth', 'oauth': {'issuer': 'https://i.test', 'client_id': 'app'}}
    return ConnectorConfig.model_validate(
        {'name': name, 'description': f'{name} connector', 'server': server, 'auth': auth}
    )


class TestSpawnedTransports:
    def test_stdio_builds_via_registry_and_is_reused(self):
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        built: list[tuple[str, str]] = []

        def factory(config, slug):
            built.append((config.name, slug))
            return _fake_toolset(config.name)

        reg = ConnectorRegistry(factory)
        docs = [_doc(_spawn_cfg())]
        caps1 = build_connector_capabilities('shaun', docs=docs, registry=reg)
        caps2 = build_connector_capabilities('shaun', docs=docs, registry=reg)
        assert caps1[0].id == 'clock' and caps2[0].id == 'clock'
        assert built == [('clock', 'shaun')]  # one build across two turns

    def test_stdio_unlinked_is_needs_setup(self):
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        reg = ConnectorRegistry(lambda c, s: _fake_toolset(c.name))
        caps = build_connector_capabilities('shaun', docs=[_doc(_spawn_cfg(mode='api_key'))], registry=reg)
        assert (caps[0].description or '').endswith('— needs setup')

    def test_start_failure_degrades_readably(self):
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        def boom(config, slug):
            raise RuntimeError('cannot spawn')

        reg = ConnectorRegistry(boom)
        caps = build_connector_capabilities('shaun', docs=[_doc(_spawn_cfg())], registry=reg)
        assert (caps[0].description or '').endswith('— needs setup')
        assert 'could not be started' in str(caps[0].get_instructions())

    def test_expired_oauth_refuses_to_spawn(self):
        """A spawned server can't refresh mid-life, so an expired token means
        reconnect rather than starting something that fails every call."""
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        TokenStore().store('shaun', 'clock', StoredTokens(access_token='a', refresh_token='r', expires_at=1.0))
        reg = ConnectorRegistry(lambda c, s: _fake_toolset(c.name))
        caps = build_connector_capabilities('shaun', docs=[_doc(_spawn_cfg(mode='oauth'))], registry=reg)
        assert 'reconnected' in str(caps[0].get_instructions())

    def test_http_oauth_expired_but_refreshable_still_exposes_tools(self):
        """Contrast: http refreshes per request, so the same token is fine there."""
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='a', refresh_token='r', expires_at=1.0))
        caps = build_connector_capabilities('shaun', docs=[_doc(_cfg(name='gh', mode='oauth'))])
        assert not (caps[0].description or '').endswith('— needs setup')

    @pytest.mark.asyncio
    async def test_inprocess_server_end_to_end(self):
        """A bundled in-process FastMCP server is reachable through the toolset."""
        from fastmcp.client import Client
        from pydantic_ai.mcp import MCPToolset

        from marcel_core.connectors.toolset import _load_inprocess_server, _spawned_toolset

        toolset = _spawned_toolset(_spawn_cfg(transport='inprocess'), 'shaun', ConnectorAuth())
        assert isinstance(toolset, MCPToolset)
        # The bundled server really is reachable and exposes its tool.
        server = _load_inprocess_server('tests.connectors.fake_inprocess_server')
        async with Client(server) as client:
            tools = await client.list_tools()
        assert 'now' in {t.name for t in tools}

    def test_inprocess_module_without_server_is_rejected(self):
        from marcel_core.connectors.toolset import _load_inprocess_server

        with pytest.raises(ValueError, match='exposes no `mcp`'):
            _load_inprocess_server('tests.connectors.fake_inprocess_broken')

    def test_spawn_env_carries_the_credential(self):
        from marcel_core.storage.credentials import save_credentials

        save_credentials('shaun', {'CLOCK_API_KEY': 'k1'})
        env = ConnectorAuth().spawn_env(_spawn_cfg(mode='api_key'), 'shaun')
        assert env == {'CLOCK_API_KEY': 'k1'}

    def test_real_stdio_toolset_constructs_without_spawning(self):
        """Constructing a stdio toolset builds the transport but spawns nothing."""
        from pydantic_ai.mcp import MCPToolset

        from marcel_core.connectors.toolset import _spawned_toolset

        toolset = _spawned_toolset(_spawn_cfg(), 'shaun', ConnectorAuth())
        assert isinstance(toolset, MCPToolset)

    def test_stdio_spawn_env_carries_credential(self):
        """The credential reaches the subprocess env (there is no per-request header)."""
        from marcel_core.storage.credentials import save_credentials

        save_credentials('shaun', {'CLOCK_API_KEY': 'k1'})
        cfg = _spawn_cfg(mode='api_key')
        assert ConnectorAuth().spawn_env(cfg, 'shaun') == {'CLOCK_API_KEY': 'k1'}

    def test_skill_bundle_omits_connector_in_backoff(self):
        from marcel_core.connectors.lifecycle import ConnectorRegistry

        def boom(config, slug):
            raise RuntimeError('cannot spawn')

        reg = ConnectorRegistry(boom)
        cfg = _cfg(transport='stdio', mode='none')
        assert connector_toolsets_for_skill(['weather'], 'shaun', docs=[_doc(cfg)], registry=reg) == []

    @pytest.mark.asyncio
    async def test_close_toolset_calls_the_client_closer(self):
        from marcel_core.connectors.toolset import _close_toolset

        closed = []

        class _WithAclose:
            async def aclose(self):
                closed.append('aclose')

        class _Bare:
            pass

        await _close_toolset(_WithAclose())
        await _close_toolset(_Bare())  # no closer → no-op, no raise
        assert closed == ['aclose']
