"""Tests for the connector token store + ConnectorAuth (FEAT-260718-230bf8, story b).

Covers the per-user Fernet token store (encrypt/refuse-plaintext/traversal-safe),
expiry logic, and ConnectorAuth's single outbound-credential decision point:
api_key (per-user vault + shared env), oauth (load/refresh/re-link), and the
structural token-passthrough prohibition.
"""

from __future__ import annotations

import stat

import pytest

from marcel_core.connectors.auth import ConnectorAuth, ConnectorNotLinked
from marcel_core.connectors.models import ConnectorConfig
from marcel_core.connectors.tokens import StoredTokens, TokenStore, TokenStoreError
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _enc_key(monkeypatch):
    """A real encryption key so the token store persists (no plaintext fallback)."""
    from marcel_core.config import settings

    monkeypatch.setattr(settings, 'marcel_credentials_key', 'test-passphrase-123')


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    return tmp_path


def _cfg(mode='api_key', *, name='weather', per_user=True, credential_keys=('WEATHER_API_KEY',), oauth=None):
    d = {
        'name': name,
        'description': 'x',
        'server': {'transport': 'http', 'url': 'https://x.test'},
        'auth': {'mode': mode, 'per_user': per_user},
    }
    if mode == 'api_key':
        d['auth']['credential_keys'] = list(credential_keys)
    if mode == 'oauth':
        d['auth']['oauth'] = oauth or {'issuer': 'https://issuer.test', 'client_id': 'marcel-app', 'scopes': ['read']}
    if mode == 'none':
        d['auth']['per_user'] = False
    return ConnectorConfig.model_validate(d)


# ---------------------------------------------------------------------------
# StoredTokens
# ---------------------------------------------------------------------------


class TestStoredTokens:
    def test_no_expiry_never_expires(self):
        assert StoredTokens(access_token='a').is_expired() is False

    def test_expired_with_skew(self):
        t = StoredTokens(access_token='a', expires_at=1000.0)
        assert t.is_expired(now=1000.0) is True  # at expiry
        assert t.is_expired(now=950.0) is True  # within default 60s skew
        assert t.is_expired(now=900.0) is False  # comfortably before

    def test_json_roundtrip(self):
        t = StoredTokens(access_token='a', refresh_token='r', expires_at=42.0, token_type='Bearer', scope='read')
        assert StoredTokens.from_json(t.to_json()) == t


# ---------------------------------------------------------------------------
# TokenStore
# ---------------------------------------------------------------------------


class TestTokenStore:
    def test_store_load_roundtrip(self, data_dir):
        store = TokenStore()
        store.store('shaun', 'weather', StoredTokens(access_token='secret-abc', refresh_token='r1'))
        loaded = store.load('shaun', 'weather')
        assert loaded is not None and loaded.access_token == 'secret-abc' and loaded.refresh_token == 'r1'

    def test_file_is_encrypted_and_0600(self, data_dir):
        store = TokenStore()
        store.store('shaun', 'weather', StoredTokens(access_token='plaintext-would-leak'))
        path = data_dir / 'users' / 'shaun' / 'connectors' / 'weather' / 'tokens.enc'
        raw = path.read_bytes()
        assert b'plaintext-would-leak' not in raw  # encrypted at rest (NFR1)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_load_unlinked_returns_none(self, data_dir):
        assert TokenStore().load('shaun', 'weather') is None

    def test_delete(self, data_dir):
        store = TokenStore()
        store.store('shaun', 'weather', StoredTokens(access_token='a'))
        assert store.delete('shaun', 'weather') is True
        assert store.load('shaun', 'weather') is None
        assert store.delete('shaun', 'weather') is False  # idempotent

    def test_refuses_plaintext_without_key(self, data_dir, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_credentials_key', None)
        with pytest.raises(TokenStoreError, match='cannot be stored unencrypted'):
            TokenStore().store('shaun', 'weather', StoredTokens(access_token='a'))

    def test_wrong_key_returns_none(self, data_dir, monkeypatch):
        from marcel_core.config import settings

        TokenStore().store('shaun', 'weather', StoredTokens(access_token='a'))
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'a-different-passphrase')
        assert TokenStore().load('shaun', 'weather') is None

    @pytest.mark.parametrize('bad', ['../etc', 'a/b', '.hidden', 'UPPER', 'a b'])
    def test_traversal_rejected(self, data_dir, bad):
        with pytest.raises(ValueError, match='unsafe'):
            TokenStore().store(bad, 'weather', StoredTokens(access_token='a'))
        with pytest.raises(ValueError, match='unsafe'):
            TokenStore().load('shaun', bad)


# ---------------------------------------------------------------------------
# ConnectorAuth — api_key
# ---------------------------------------------------------------------------


class TestApiKeyAuth:
    @pytest.mark.asyncio
    async def test_per_user_key_from_vault(self, data_dir):
        from marcel_core.storage.credentials import save_credentials

        save_credentials('shaun', {'WEATHER_API_KEY': 'user-key-xyz'})
        headers = await ConnectorAuth().outbound_headers(_cfg('api_key'), 'shaun')
        assert headers == {'Authorization': 'Bearer user-key-xyz'}

    @pytest.mark.asyncio
    async def test_per_user_missing_is_not_linked(self, data_dir):
        with pytest.raises(ConnectorNotLinked, match='WEATHER_API_KEY'):
            await ConnectorAuth().outbound_headers(_cfg('api_key'), 'shaun')

    @pytest.mark.asyncio
    async def test_shared_key_from_env(self, data_dir, monkeypatch):
        monkeypatch.setenv('SHARED_KEY', 'shared-abc')
        cfg = _cfg('api_key', per_user=False, credential_keys=('SHARED_KEY',))
        headers = await ConnectorAuth().outbound_headers(cfg, 'shaun')
        assert headers == {'Authorization': 'Bearer shared-abc'}

    @pytest.mark.asyncio
    async def test_shared_key_missing_is_readable(self, data_dir, monkeypatch):
        monkeypatch.delenv('SHARED_KEY', raising=False)
        cfg = _cfg('api_key', per_user=False, credential_keys=('SHARED_KEY',))
        with pytest.raises(ConnectorNotLinked, match='not configured'):
            await ConnectorAuth().outbound_headers(cfg, 'shaun')


# ---------------------------------------------------------------------------
# ConnectorAuth — none + passthrough prohibition
# ---------------------------------------------------------------------------


class TestLinkageError:
    """The sync linkage probe the composition root uses at agent-build time."""

    def test_none_mode_is_always_usable(self, data_dir):
        assert ConnectorAuth().linkage_error(_cfg('none'), 'shaun') is None

    def test_shared_key_present_and_missing(self, data_dir, monkeypatch):
        cfg = _cfg('api_key', per_user=False, credential_keys=('SHARED_KEY',))
        monkeypatch.setenv('SHARED_KEY', 'v')
        assert ConnectorAuth().linkage_error(cfg, 'shaun') is None
        monkeypatch.delenv('SHARED_KEY')
        assert 'not configured' in (ConnectorAuth().linkage_error(cfg, 'shaun') or '')

    def test_expired_without_refresh_token_needs_relink(self, data_dir):
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='a', expires_at=1.0))
        assert 'reconnected' in (ConnectorAuth().linkage_error(_cfg('oauth', name='gh'), 'shaun') or '')

    def test_expired_with_refresh_token_counts_as_linked(self, data_dir):
        # The per-request flow will refresh it; the catalog should still expose tools.
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='a', refresh_token='r', expires_at=1.0))
        assert ConnectorAuth().linkage_error(_cfg('oauth', name='gh'), 'shaun') is None


class TestOutboundEnv:
    """Credential delivery for spawned (stdio/inprocess) servers."""

    @pytest.mark.asyncio
    async def test_api_key_uses_the_habitats_own_var_name(self, data_dir):
        from marcel_core.storage.credentials import save_credentials

        save_credentials('shaun', {'WEATHER_API_KEY': 'user-key'})
        env = await ConnectorAuth().outbound_env(_cfg('api_key'), 'shaun')
        assert env == {'WEATHER_API_KEY': 'user-key'}

    @pytest.mark.asyncio
    async def test_oauth_uses_the_documented_token_var(self, data_dir):
        from marcel_core.connectors.auth import OAUTH_TOKEN_ENV

        TokenStore().store('shaun', 'gh', StoredTokens(access_token='tok'))
        env = await ConnectorAuth().outbound_env(_cfg('oauth', name='gh'), 'shaun')
        assert env == {OAUTH_TOKEN_ENV: 'tok'}

    @pytest.mark.asyncio
    async def test_none_delivers_nothing(self, data_dir):
        assert await ConnectorAuth().outbound_env(_cfg('none'), 'shaun') == {}

    @pytest.mark.asyncio
    async def test_unlinked_still_refuses(self, data_dir):
        with pytest.raises(ConnectorNotLinked):
            await ConnectorAuth().outbound_env(_cfg('api_key'), 'shaun')


class TestNoneAndPassthrough:
    @pytest.mark.asyncio
    async def test_none_has_no_headers(self, data_dir):
        assert await ConnectorAuth().outbound_headers(_cfg('none'), 'shaun') == {}

    @pytest.mark.asyncio
    async def test_marcel_token_never_forwarded(self, data_dir, monkeypatch):
        """A Marcel-audience token sitting in the environment/config never reaches
        an outbound header — ConnectorAuth only sources per-connector credentials."""
        from marcel_core.storage.credentials import save_credentials

        monkeypatch.setenv('MARCEL_API_TOKEN', 'marcel-secret-do-not-forward')
        save_credentials('shaun', {'WEATHER_API_KEY': 'weather-key'})
        headers = await ConnectorAuth().outbound_headers(_cfg('api_key'), 'shaun')
        assert 'marcel-secret-do-not-forward' not in str(headers)
        assert headers == {'Authorization': 'Bearer weather-key'}


# ---------------------------------------------------------------------------
# ConnectorAuth — oauth
# ---------------------------------------------------------------------------


class _FakeRefresher:
    def __init__(self, new_tokens):
        self.new_tokens = new_tokens
        self.calls: list[str] = []

    async def refresh(self, config, refresh_token):
        self.calls.append(refresh_token)
        return self.new_tokens


class TestOAuthAuth:
    @pytest.mark.asyncio
    async def test_valid_token_injected(self, data_dir):
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='fresh-access'))
        cfg = _cfg('oauth', name='gh', per_user=True)
        headers = await ConnectorAuth().outbound_headers(cfg, 'shaun')
        assert headers == {'Authorization': 'Bearer fresh-access'}

    @pytest.mark.asyncio
    async def test_unlinked_is_readable(self, data_dir):
        with pytest.raises(ConnectorNotLinked, match='not linked'):
            await ConnectorAuth().outbound_headers(_cfg('oauth', name='gh'), 'shaun')

    @pytest.mark.asyncio
    async def test_expired_refreshes_and_persists(self, data_dir):
        store = TokenStore()
        store.store('shaun', 'gh', StoredTokens(access_token='old', refresh_token='r1', expires_at=1.0))
        refresher = _FakeRefresher(StoredTokens(access_token='new-access', expires_at=None))
        auth = ConnectorAuth(store=store, refresher=refresher)
        headers = await auth.outbound_headers(_cfg('oauth', name='gh'), 'shaun')
        assert headers == {'Authorization': 'Bearer new-access'}
        assert refresher.calls == ['r1']
        # Persisted, and the old refresh token is carried forward.
        persisted = store.load('shaun', 'gh')
        assert persisted is not None and persisted.access_token == 'new-access' and persisted.refresh_token == 'r1'

    @pytest.mark.asyncio
    async def test_refresh_keeps_new_refresh_token_when_provided(self, data_dir):
        store = TokenStore()
        store.store('shaun', 'gh', StoredTokens(access_token='old', refresh_token='r1', expires_at=1.0))
        # Provider rotates the refresh token — the new one must be persisted.
        refresher = _FakeRefresher(StoredTokens(access_token='new', refresh_token='r2'))
        await ConnectorAuth(store=store, refresher=refresher).outbound_headers(_cfg('oauth', name='gh'), 'shaun')
        persisted = store.load('shaun', 'gh')
        assert persisted is not None and persisted.refresh_token == 'r2'

    @pytest.mark.asyncio
    async def test_expired_no_refresh_token_is_relink(self, data_dir):
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='old', expires_at=1.0))
        with pytest.raises(ConnectorNotLinked, match='reconnected'):
            await ConnectorAuth().outbound_headers(_cfg('oauth', name='gh'), 'shaun')

    @pytest.mark.asyncio
    async def test_expired_no_refresher_is_relink(self, data_dir):
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='old', refresh_token='r1', expires_at=1.0))
        # ConnectorAuth without a refresher wired cannot refresh.
        with pytest.raises(ConnectorNotLinked, match='reconnected'):
            await ConnectorAuth().outbound_headers(_cfg('oauth', name='gh'), 'shaun')


class TestSpawnEnvBranches:
    """spawn_env is the sync credential path for stdio/inprocess."""

    @pytest.mark.asyncio
    async def test_shared_key_from_env(self, data_dir, monkeypatch):
        monkeypatch.setenv('SHARED_KEY', 'sh')
        cfg = _cfg('api_key', per_user=False, credential_keys=('SHARED_KEY',))
        assert ConnectorAuth().spawn_env(cfg, 'shaun') == {'SHARED_KEY': 'sh'}

    def test_shared_key_missing_raises(self, data_dir, monkeypatch):
        monkeypatch.delenv('SHARED_KEY', raising=False)
        cfg = _cfg('api_key', per_user=False, credential_keys=('SHARED_KEY',))
        with pytest.raises(ConnectorNotLinked, match='not configured'):
            ConnectorAuth().spawn_env(cfg, 'shaun')

    def test_per_user_key_missing_raises(self, data_dir):
        with pytest.raises(ConnectorNotLinked, match='WEATHER_API_KEY'):
            ConnectorAuth().spawn_env(_cfg('api_key'), 'shaun')

    def test_oauth_valid_token(self, data_dir):
        from marcel_core.connectors.auth import OAUTH_TOKEN_ENV

        TokenStore().store('shaun', 'gh', StoredTokens(access_token='tok'))
        assert ConnectorAuth().spawn_env(_cfg('oauth', name='gh'), 'shaun') == {OAUTH_TOKEN_ENV: 'tok'}

    def test_oauth_expired_refuses(self, data_dir):
        """No sync refresh is possible, so an expired token must not be spawned with."""
        TokenStore().store('shaun', 'gh', StoredTokens(access_token='old', refresh_token='r', expires_at=1.0))
        with pytest.raises(ConnectorNotLinked, match='reconnected'):
            ConnectorAuth().spawn_env(_cfg('oauth', name='gh'), 'shaun')

    def test_none_is_empty(self, data_dir):
        assert ConnectorAuth().spawn_env(_cfg('none'), 'shaun') == {}
