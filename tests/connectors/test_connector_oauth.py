"""Tests for the OAuth 2.1 + PKCE linking flow and callback route (story c).

Fakes the provider with an httpx MockTransport (discovery + token endpoints) so
the whole dance runs without a network or a real IdP.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from marcel_core.connectors.models import ConnectorConfig
from marcel_core.connectors.oauth import (
    ConnectorOAuth,
    OAuthError,
    PendingLinkStore,
    _PendingLink,
    generate_pkce,
    public_base_url,
    redirect_uri,
)
from marcel_core.connectors.tokens import StoredTokens, TokenStore
from marcel_core.storage import _root

ISSUER = 'https://issuer.test'
AUTH_EP = 'https://issuer.test/authorize'
TOKEN_EP = 'https://issuer.test/token'


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from marcel_core.config import settings

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(settings, 'marcel_credentials_key', 'test-passphrase-123')
    monkeypatch.setattr(settings, 'marcel_public_url', 'https://marcel.test')


def _cfg(name='gh', *, client_secret_key=None, scopes=('repo',)):
    return ConnectorConfig.model_validate(
        {
            'name': name,
            'description': 'x',
            'server': {'transport': 'http', 'url': 'https://mcp.test'},
            'auth': {
                'mode': 'oauth',
                'oauth': {
                    'issuer': ISSUER,
                    'client_id': 'marcel-app',
                    'client_secret_key': client_secret_key,
                    'scopes': list(scopes),
                },
            },
        }
    )


def _provider(token_response=None, token_status=200, *, discovery_status=200, record=None):
    """An httpx client faking the provider's discovery + token endpoints."""
    token_response = (
        token_response
        if token_response is not None
        else {
            'access_token': 'access-1',
            'refresh_token': 'refresh-1',
            'expires_in': 3600,
            'token_type': 'Bearer',
            'scope': 'repo',
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith('/.well-known/'):
            if discovery_status != 200:
                return httpx.Response(discovery_status)
            return httpx.Response(200, json={'authorization_endpoint': AUTH_EP, 'token_endpoint': TOKEN_EP})
        if str(request.url) == TOKEN_EP:
            if record is not None:
                record.append(dict(parse_qs(request.content.decode())))
            return httpx.Response(token_status, json=token_response)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# PKCE + base URL
# ---------------------------------------------------------------------------


class TestPkceAndBaseUrl:
    def test_pkce_challenge_is_s256_of_verifier(self):
        verifier, challenge = generate_pkce()
        assert 43 <= len(verifier) <= 128
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        assert challenge == expected
        assert '=' not in challenge  # unpadded base64url

    def test_pkce_is_random_per_call(self):
        assert generate_pkce()[0] != generate_pkce()[0]

    def test_redirect_uri(self):
        assert redirect_uri() == 'https://marcel.test/connectors/callback'

    def test_unset_base_is_readable(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_public_url', None)
        with pytest.raises(OAuthError, match='public address'):
            public_base_url()

    def test_insecure_base_rejected(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_public_url', 'http://marcel.example.org')
        with pytest.raises(OAuthError, match='https'):
            public_base_url()

    def test_localhost_http_allowed_for_dev(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_public_url', 'http://localhost:7421')
        assert public_base_url() == 'http://localhost:7421'


# ---------------------------------------------------------------------------
# PendingLinkStore
# ---------------------------------------------------------------------------


class TestPendingLinkStore:
    def _link(self):
        return _PendingLink(slug='shaun', connector='gh', code_verifier='v', redirect_uri='https://x/cb')

    def test_consume_is_single_use(self):
        store = PendingLinkStore()
        store.put('s1', self._link())
        assert store.consume('s1') is not None
        assert store.consume('s1') is None  # replay rejected

    def test_unknown_state(self):
        assert PendingLinkStore().consume('nope') is None

    def test_expired_is_dropped(self):
        store = PendingLinkStore()
        link = self._link()
        link.created_at = 0.0  # ancient
        store.put('s1', link)
        assert store.consume('s1') is None


# ---------------------------------------------------------------------------
# start_link
# ---------------------------------------------------------------------------


class TestStartLink:
    @pytest.mark.asyncio
    async def test_builds_authorization_url(self):
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(_cfg(), 'shaun')
        parsed = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        assert f'{parsed.scheme}://{parsed.netloc}{parsed.path}' == AUTH_EP
        assert q['response_type'] == 'code'
        assert q['client_id'] == 'marcel-app'
        assert q['redirect_uri'] == 'https://marcel.test/connectors/callback'
        assert q['code_challenge_method'] == 'S256'
        assert q['scope'] == 'repo'
        assert len(q['state']) >= 40  # 256 bits, base64url
        assert '=' not in q['code_challenge']

    @pytest.mark.asyncio
    async def test_verifier_never_in_the_url(self):
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(_cfg(), 'shaun')
        # The verifier stays server-side; only its S256 challenge travels.
        state = parse_qs(urlparse(url).query)['state'][0]
        pending = flow._pending.consume(state)
        assert pending is not None and pending.code_verifier not in url

    @pytest.mark.asyncio
    async def test_non_oauth_connector_refused(self):
        cfg = ConnectorConfig.model_validate(
            {
                'name': 'w',
                'description': 'x',
                'server': {'transport': 'http', 'url': 'https://x.test'},
                'auth': {'mode': 'api_key', 'credential_keys': ['K']},
            }
        )
        with pytest.raises(OAuthError, match='does not use account linking'):
            await ConnectorOAuth(http_client=_provider()).start_link(cfg, 'shaun')

    @pytest.mark.asyncio
    async def test_discovery_failure_is_readable(self):
        flow = ConnectorOAuth(http_client=_provider(discovery_status=500))
        with pytest.raises(OAuthError, match='Could not reach'):
            await flow.start_link(_cfg(), 'shaun')

    @pytest.mark.asyncio
    async def test_no_scopes_omits_scope_param(self):
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(_cfg(scopes=()), 'shaun')
        assert 'scope=' not in url

    @pytest.mark.asyncio
    async def test_discovery_network_error_is_readable(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('network down')

        flow = ConnectorOAuth(http_client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
        with pytest.raises(OAuthError, match='Could not reach'):
            await flow.start_link(_cfg(), 'shaun')

    @pytest.mark.asyncio
    async def test_discovery_falls_back_to_openid_configuration(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith('/oauth-authorization-server'):
                return httpx.Response(200, json={})  # present but useless
            if request.url.path.endswith('/openid-configuration'):
                return httpx.Response(200, json={'authorization_endpoint': AUTH_EP, 'token_endpoint': TOKEN_EP})
            return httpx.Response(404)

        flow = ConnectorOAuth(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        url = await flow.start_link(_cfg(), 'shaun')
        assert url.startswith(AUTH_EP)

    @pytest.mark.asyncio
    async def test_insecure_issuer_rejected(self):
        cfg = _cfg()
        cfg.auth.oauth.issuer = 'http://issuer.test'  # type: ignore[union-attr]
        with pytest.raises(OAuthError, match='https'):
            await ConnectorOAuth(http_client=_provider()).start_link(cfg, 'shaun')


# ---------------------------------------------------------------------------
# complete_link
# ---------------------------------------------------------------------------


def _lookup_for(cfg):
    return lambda name, slug: cfg if name == cfg.name else None


class TestCompleteLink:
    @pytest.mark.asyncio
    async def test_end_to_end_stores_encrypted_tokens(self, tmp_path):
        cfg = _cfg()
        sent: list[dict] = []
        flow = ConnectorOAuth(http_client=_provider(record=sent))
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]

        slug, connector = await flow.complete_link(_lookup_for(cfg), 'the-code', state)
        assert (slug, connector) == ('shaun', 'gh')

        # The exchange carried the verifier and the same redirect_uri.
        assert sent[0]['grant_type'] == ['authorization_code']
        assert sent[0]['code'] == ['the-code']
        assert sent[0]['code_verifier'][0]
        assert sent[0]['redirect_uri'] == ['https://marcel.test/connectors/callback']

        stored = TokenStore().load('shaun', 'gh')
        assert stored is not None and stored.access_token == 'access-1'
        assert stored.refresh_token == 'refresh-1' and stored.expires_at is not None
        # On disk it is encrypted.
        raw = (tmp_path / 'users' / 'shaun' / 'connectors' / 'gh' / 'tokens.enc').read_bytes()
        assert b'access-1' not in raw

    @pytest.mark.asyncio
    async def test_slug_comes_from_pending_not_request(self):
        """A forged callback cannot bind the connection to another user."""
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        slug, _ = await flow.complete_link(_lookup_for(cfg), 'code', state)
        assert slug == 'shaun'  # no request field could have changed this
        assert TokenStore().load('bob', 'gh') is None

    @pytest.mark.asyncio
    async def test_replayed_state_rejected(self):
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        await flow.complete_link(_lookup_for(cfg), 'code', state)
        with pytest.raises(OAuthError, match='expired or was already used'):
            await flow.complete_link(_lookup_for(cfg), 'code', state)

    @pytest.mark.asyncio
    async def test_unknown_state_rejected(self):
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        with pytest.raises(OAuthError, match='expired or was already used'):
            await flow.complete_link(_lookup_for(cfg), 'code', 'forged-state')

    @pytest.mark.asyncio
    async def test_token_endpoint_error_is_readable_and_quiet(self, caplog):
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider(token_response={'error': 'bad'}, token_status=400))
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        with pytest.raises(OAuthError, match='rejected the connection'):
            await flow.complete_link(_lookup_for(cfg), 'code', state)
        # The provider body is never echoed into logs.
        assert 'bad' not in caplog.text

    @pytest.mark.asyncio
    async def test_missing_access_token_is_readable(self):
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider(token_response={'token_type': 'Bearer'}))
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        with pytest.raises(OAuthError, match='did not return an access token'):
            await flow.complete_link(_lookup_for(cfg), 'code', state)

    @pytest.mark.asyncio
    async def test_confidential_client_sends_secret(self, monkeypatch):
        monkeypatch.setenv('GH_CLIENT_SECRET', 'sh-secret')
        cfg = _cfg(client_secret_key='GH_CLIENT_SECRET')
        sent: list[dict] = []
        flow = ConnectorOAuth(http_client=_provider(record=sent))
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        await flow.complete_link(_lookup_for(cfg), 'code', state)
        assert sent[0]['client_secret'] == ['sh-secret']

    @pytest.mark.asyncio
    async def test_missing_client_secret_is_readable(self, monkeypatch):
        monkeypatch.delenv('GH_CLIENT_SECRET', raising=False)
        cfg = _cfg(client_secret_key='GH_CLIENT_SECRET')
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        with pytest.raises(OAuthError, match='missing its server-side credential'):
            await flow.complete_link(_lookup_for(cfg), 'code', state)

    @pytest.mark.asyncio
    async def test_token_network_error_is_readable(self):
        cfg = _cfg()
        calls = {'n': 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith('/.well-known/'):
                return httpx.Response(200, json={'authorization_endpoint': AUTH_EP, 'token_endpoint': TOKEN_EP})
            calls['n'] += 1
            raise httpx.ConnectError('network down')

        flow = ConnectorOAuth(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        with pytest.raises(OAuthError, match='Could not reach the login service'):
            await flow.complete_link(_lookup_for(cfg), 'code', state)
        assert calls['n'] == 1

    @pytest.mark.asyncio
    async def test_unknown_connector_after_link_started(self):
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        with pytest.raises(OAuthError, match='no longer available'):
            await flow.complete_link(lambda name, slug: None, 'code', state)

    @pytest.mark.asyncio
    async def test_audit_line_written(self):
        from marcel_core.storage.approvals import read_audit

        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        await flow.complete_link(_lookup_for(cfg), 'code', state)
        entries = [e for e in read_audit() if e.get('type') == 'connector_link']
        assert entries and entries[-1]['user'] == 'shaun' and entries[-1]['connector'] == 'gh'
        # No token material in the audit trail.
        assert 'access-1' not in json.dumps(entries)


# ---------------------------------------------------------------------------
# refresh + unlink
# ---------------------------------------------------------------------------


class TestRefreshAndUnlink:
    @pytest.mark.asyncio
    async def test_refresh_exchanges_refresh_token(self):
        sent: list[dict] = []
        flow = ConnectorOAuth(http_client=_provider(record=sent))
        tokens = await flow.refresh(_cfg(), 'refresh-old')
        assert tokens.access_token == 'access-1'
        assert sent[0]['grant_type'] == ['refresh_token']
        assert sent[0]['refresh_token'] == ['refresh-old']

    @pytest.mark.asyncio
    async def test_refresh_satisfies_connector_auth(self):
        """ConnectorOAuth plugs straight into ConnectorAuth as its TokenRefresher."""
        from marcel_core.connectors.auth import ConnectorAuth

        store = TokenStore()
        store.store('shaun', 'gh', StoredTokens(access_token='old', refresh_token='r1', expires_at=1.0))
        flow = ConnectorOAuth(store=store, http_client=_provider())
        headers = await ConnectorAuth(store=store, refresher=flow).outbound_headers(_cfg(), 'shaun')
        assert headers == {'Authorization': 'Bearer access-1'}

    def test_unlink_deletes_and_audits(self):
        from marcel_core.storage.approvals import read_audit

        store = TokenStore()
        store.store('shaun', 'gh', StoredTokens(access_token='a'))
        flow = ConnectorOAuth(store=store)
        assert flow.unlink('shaun', 'gh') is True
        assert store.load('shaun', 'gh') is None
        assert any(e.get('type') == 'connector_unlink' for e in read_audit())

    def test_unlink_when_not_linked(self):
        assert ConnectorOAuth().unlink('shaun', 'gh') is False


# ---------------------------------------------------------------------------
# callback route
# ---------------------------------------------------------------------------


class TestCallbackRoute:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import marcel_core.api.connectors as api

        app = FastAPI()
        app.include_router(api.router)
        return TestClient(app), api

    def test_success_page(self, client, monkeypatch):
        tc, api = client
        cfg = _cfg()
        flow = ConnectorOAuth(http_client=_provider())
        monkeypatch.setattr(api, '_flow', flow)
        monkeypatch.setattr(api, '_lookup', _lookup_for(cfg))

        import asyncio

        url = asyncio.get_event_loop().run_until_complete(flow.start_link(cfg, 'shaun'))
        state = parse_qs(urlparse(url).query)['state'][0]

        resp = tc.get('/connectors/callback', params={'code': 'c', 'state': state})
        assert resp.status_code == 200
        assert 'Connected' in resp.text
        assert 'gh' in resp.text

    def test_provider_error_page(self, client):
        tc, _ = client
        resp = tc.get('/connectors/callback', params={'error': 'access_denied'})
        assert resp.status_code == 400
        assert 'Connection cancelled' in resp.text
        assert 'access_denied' not in resp.text  # not echoed back

    def test_missing_params_page(self, client):
        tc, _ = client
        resp = tc.get('/connectors/callback', params={'code': 'only-code'})
        assert resp.status_code == 400
        assert 'Something was missing' in resp.text

    def test_bad_state_is_readable_not_a_traceback(self, client):
        tc, _ = client
        resp = tc.get('/connectors/callback', params={'code': 'c', 'state': 'forged'})
        assert resp.status_code == 400
        assert 'expired or was already used' in resp.text
        assert 'Traceback' not in resp.text

    def test_unexpected_failure_shows_generic_page(self, client, monkeypatch):
        """An internal error never leaks a traceback to this family-facing page."""
        tc, api = client

        class _Boom:
            async def complete_link(self, *a, **kw):
                raise RuntimeError('internal detail that must not leak')

        monkeypatch.setattr(api, '_flow', _Boom())
        resp = tc.get('/connectors/callback', params={'code': 'c', 'state': 's'})
        assert resp.status_code == 400
        assert 'Something went wrong' in resp.text
        assert 'internal detail' not in resp.text
        assert 'Traceback' not in resp.text

    def test_lookup_resolves_via_loader_scoping(self, tmp_path, monkeypatch):
        """The route's connector lookup goes through the normal scoping chain."""
        import marcel_core.api.connectors as api
        from marcel_core.config import settings

        zoo = tmp_path / 'zoo'
        cdir = zoo / 'connectors' / 'gh'
        cdir.mkdir(parents=True)
        (cdir / 'connector.yaml').write_text(
            'name: gh\ndescription: GitHub\n'
            'server: {transport: http, url: https://mcp.test}\n'
            'auth:\n  mode: oauth\n  oauth: {issuer: https://issuer.test, client_id: marcel-app}\n'
        )
        monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))

        assert api._lookup('gh', 'shaun') is not None
        assert api._lookup('nope', 'shaun') is None


class TestSecurityAuditRegressions:
    """Fixes from the story-f security audit."""

    @pytest.mark.asyncio
    async def test_start_link_enforces_admin_scope_itself(self):
        """MEDIUM-5: the scope check cannot be left to an unwritten call site."""
        cfg = _cfg()
        cfg.scope = cfg.scope.__class__.ADMIN
        flow = ConnectorOAuth(http_client=_provider())
        with pytest.raises(OAuthError, match='not available for your account'):
            await flow.start_link(cfg, 'shaun', role='user')
        # An admin may still link it.
        url = await flow.start_link(cfg, 'shaun', role='admin')
        assert url.startswith(AUTH_EP)

    @pytest.mark.asyncio
    async def test_hostile_token_type_is_not_interpolated(self):
        """LOW-2: token_type comes from provider JSON straight into a header."""
        from marcel_core.connectors.auth import ConnectorAuth

        cfg = _cfg()
        flow = ConnectorOAuth(
            http_client=_provider(token_response={'access_token': 'a', 'token_type': 'Bearer\r\nX-Evil: 1'})
        )
        url = await flow.start_link(cfg, 'shaun')
        state = parse_qs(urlparse(url).query)['state'][0]
        await flow.complete_link(_lookup_for(cfg), 'code', state)
        headers = await ConnectorAuth().outbound_headers(cfg, 'shaun')
        assert headers == {'Authorization': 'Bearer a'}  # fell back, no injection

    def test_callback_page_escapes_interpolated_values(self):
        from marcel_core.api.connectors import _page

        resp = _page('Could not connect', "<script>alert('x')</script>", ok=False)
        rendered = bytes(resp.body).decode()
        assert '<script>' not in rendered
        assert '&lt;script&gt;' in rendered
