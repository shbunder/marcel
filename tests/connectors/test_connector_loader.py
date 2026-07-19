"""Tests for connector.yaml schema, discovery, scoping and isolation (FEAT-260718-230bf8)."""

from __future__ import annotations

import logging
import textwrap

import pytest
from pydantic import ValidationError

from marcel_core.connectors.loader import (
    _connector_dirs,
    get_connector,
    load_connectors,
    validate_connector_config,
)
from marcel_core.connectors.models import (
    AuthMode,
    ConnectorConfig,
    DefaultEnabled,
    Discovery,
    Scope,
    Transport,
)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Set up zoo + data roots; return a helper that writes a connector into a scope."""
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    data = tmp_path / 'data'
    (zoo / 'connectors').mkdir(parents=True)
    (data).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data))

    def make(scope, name, *, yaml_text=None, setup_md=None, user='shaun'):
        if scope == 'zoo-global':
            base = zoo / 'connectors' / name
        elif scope == 'zoo-user':
            base = zoo / 'users' / user / 'connectors' / name
        elif scope == 'data-user':
            base = data / 'users' / user / 'connectors' / name
        else:
            raise ValueError(scope)
        base.mkdir(parents=True, exist_ok=True)
        if yaml_text is not None:
            (base / 'connector.yaml').write_text(textwrap.dedent(yaml_text))
        if setup_md is not None:
            (base / 'SETUP.md').write_text(setup_md)
        return base

    return make


def _http_api_key(name='weather'):
    return f"""
    name: {name}
    description: Weather over HTTP
    server:
      transport: http
      url: https://mcp.example.test/sse
    auth:
      mode: api_key
      per_user: true
      credential_keys: [WEATHER_API_KEY]
    """


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


class TestConnectorConfigSchema:
    def test_valid_http_api_key(self):
        c = ConnectorConfig.model_validate(
            {
                'name': 'weather',
                'description': 'x',
                'server': {'transport': 'http', 'url': 'https://x.test'},
                'auth': {'mode': 'api_key', 'credential_keys': ['K']},
            }
        )
        assert c.server.transport is Transport.HTTP
        assert c.auth.mode is AuthMode.API_KEY
        assert c.discovery is Discovery.DEFERRED  # default
        assert c.scope is Scope.ALL  # default
        assert c.default_enabled is DefaultEnabled.ALL  # default

    def test_valid_stdio_oauth(self):
        c = ConnectorConfig.model_validate(
            {
                'name': 'gh',
                'description': 'GitHub',
                'server': {'transport': 'stdio', 'command': ['gh-mcp', '--stdio']},
                'auth': {
                    'mode': 'oauth',
                    'oauth': {'issuer': 'https://github.com', 'client_id': 'marcel-app', 'scopes': ['repo']},
                },
                'discovery': 'eager',
                'scope': 'admin',
            }
        )
        assert c.server.command == ['gh-mcp', '--stdio']
        assert c.auth.oauth is not None and c.auth.oauth.scopes == ['repo']
        assert c.discovery is Discovery.EAGER
        assert c.scope is Scope.ADMIN

    def test_valid_inprocess_none(self):
        c = ConnectorConfig.model_validate(
            {
                'name': 'clock',
                'description': 'clock',
                'server': {'transport': 'inprocess', 'module': 'connectors.clock.server'},
                'auth': {'mode': 'none', 'per_user': False},
                'scope': 'admin',
            }
        )
        assert c.server.module == 'connectors.clock.server'
        assert c.auth.mode is AuthMode.NONE

    def test_http_requires_url(self):
        with pytest.raises(ValidationError, match="requires server.'url'"):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http'},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_stdio_requires_command(self):
        with pytest.raises(ValidationError, match="requires server.'command'"):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'stdio'},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_stray_locator_rejected(self):
        with pytest.raises(ValidationError, match="stray server.'command'"):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test', 'command': ['nope']},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_oauth_requires_oauth_block(self):
        with pytest.raises(ValidationError, match='requires an auth.oauth block'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'oauth'},
                }
            )

    def test_oauth_forbids_credential_keys(self):
        with pytest.raises(ValidationError, match='must not set auth.credential_keys'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {
                        'mode': 'oauth',
                        'oauth': {'issuer': 'https://i.test', 'client_id': 'a'},
                        'credential_keys': ['K'],
                    },
                }
            )

    def test_oauth_requires_client_id(self):
        # OAuth 2.1 needs a client registration; there is no dynamic registration.
        with pytest.raises(ValidationError, match='client_id'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'oauth', 'oauth': {'issuer': 'https://i.test'}},
                }
            )

    def test_api_key_requires_credential_keys(self):
        with pytest.raises(ValidationError, match='requires auth.credential_keys'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'api_key'},
                }
            )

    def test_none_cannot_be_per_user(self):
        with pytest.raises(ValidationError, match='cannot be per_user'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'none', 'per_user': True},
                }
            )

    def test_api_key_forbids_oauth_block(self):
        with pytest.raises(ValidationError, match='must not set auth.oauth'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {
                        'mode': 'api_key',
                        'credential_keys': ['K'],
                        'oauth': {'issuer': 'https://i.test', 'client_id': 'a'},
                    },
                }
            )

    def test_none_forbids_credentials(self):
        with pytest.raises(ValidationError, match='takes no credential_keys'):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'none', 'per_user': False, 'credential_keys': ['K']},
                }
            )

    def test_unknown_top_level_key_forbidden(self):
        with pytest.raises(ValidationError):
            ConnectorConfig.model_validate(
                {
                    'name': 'x',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'none', 'per_user': False},
                    'bogus': 1,
                }
            )

    def test_blank_name_rejected(self):
        with pytest.raises(ValidationError):
            ConnectorConfig.model_validate(
                {
                    'name': '',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )


# ---------------------------------------------------------------------------
# validate_connector_config
# ---------------------------------------------------------------------------


class TestValidateConnectorConfig:
    def test_ok(self):
        import yaml as _yaml

        cfg, err = validate_connector_config(_yaml.safe_load(_http_api_key('weather')), 'weather')
        assert err is None and cfg is not None and cfg.name == 'weather'

    def test_non_mapping(self):
        cfg, err = validate_connector_config(['not', 'a', 'map'], 'weather')
        assert cfg is None and err is not None and 'must be a mapping' in err

    def test_name_must_equal_dir(self):
        import yaml as _yaml

        cfg, err = validate_connector_config(_yaml.safe_load(_http_api_key('weather')), 'climate')
        assert cfg is None and err is not None and 'must equal the directory name' in err

    def test_schema_error_is_located(self):
        cfg, err = validate_connector_config(
            {
                'name': 'x',
                'description': 'x',
                'server': {'transport': 'http'},
                'auth': {'mode': 'none', 'per_user': False},
            },
            'x',
        )
        assert cfg is None and err is not None
        assert 'connector.yaml invalid at' in err and 'server' in err


# ---------------------------------------------------------------------------
# discovery + scoping chain
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_no_user_only_global(self, roots):
        assert [src for _p, src in _connector_dirs(None)] == ['zoo-global']

    def test_all_three_roots(self, roots):
        roots('zoo-user', 'a', yaml_text=_http_api_key('a'))
        roots('data-user', 'b', yaml_text=_http_api_key('b'))
        assert [src for _p, src in _connector_dirs('shaun')] == ['zoo-global', 'zoo-user', 'data-user']

    def test_missing_roots_absent(self, roots):
        assert [src for _p, src in _connector_dirs('shaun')] == ['zoo-global']

    def test_loads_and_sorts(self, roots):
        roots('zoo-global', 'zebra', yaml_text=_http_api_key('zebra'))
        roots('zoo-global', 'alpha', yaml_text=_http_api_key('alpha'))
        assert [d.name for d in load_connectors('shaun')] == ['alpha', 'zebra']

    def test_hidden_and_underscore_skipped(self, roots):
        roots('zoo-global', '.hidden', yaml_text=_http_api_key('hidden'))
        roots('zoo-global', '_internal', yaml_text=_http_api_key('internal'))
        roots('zoo-global', 'ok', yaml_text=_http_api_key('ok'))
        assert [d.name for d in load_connectors('shaun')] == ['ok']

    def test_most_specific_wins_with_shadow_log(self, roots, caplog):
        roots('zoo-global', 'weather', yaml_text=_http_api_key('weather'))
        # user override lives in data-user
        roots('data-user', 'weather', yaml_text=_http_api_key('weather'))
        with caplog.at_level(logging.INFO, logger='marcel_core.connectors.loader'):
            docs = load_connectors('shaun')
        weather = next(d for d in docs if d.name == 'weather')
        assert weather.source == 'data-user'
        assert any('shadows' in r.getMessage() for r in caplog.records)


class TestIsolation:
    def test_bad_connector_skipped_others_load(self, roots, caplog):
        roots('zoo-global', 'good', yaml_text=_http_api_key('good'))
        roots('zoo-global', 'broken', yaml_text='name: broken\ndescription: x\nserver:\n  transport: http\n')  # no url
        roots('zoo-global', 'noyaml')  # dir with no connector.yaml
        with caplog.at_level(logging.WARNING, logger='marcel_core.connectors.loader'):
            docs = load_connectors('shaun')
        assert [d.name for d in docs] == ['good']
        msgs = ' '.join(r.getMessage() for r in caplog.records)
        assert 'broken' in msgs and 'noyaml' in msgs

    def test_unreadable_yaml_skipped(self, roots):
        roots('zoo-global', 'bad', yaml_text='{ this: is: not: valid: yaml')
        roots('zoo-global', 'good', yaml_text=_http_api_key('good'))
        assert [d.name for d in load_connectors('shaun')] == ['good']

    def test_name_mismatch_skipped(self, roots):
        roots('zoo-global', 'climate', yaml_text=_http_api_key('weather'))  # name != dir
        assert load_connectors('shaun') == []


# ---------------------------------------------------------------------------
# scope gating
# ---------------------------------------------------------------------------


class TestScopeGating:
    def test_admin_connector_hidden_from_user(self, roots):
        roots(
            'zoo-global',
            'secret',
            yaml_text="""
            name: secret
            description: admin only
            server: {transport: http, url: https://x.test}
            auth: {mode: api_key, credential_keys: [K]}
            scope: admin
            """,
        )
        roots('zoo-global', 'weather', yaml_text=_http_api_key('weather'))
        assert [d.name for d in load_connectors('shaun', role='user')] == ['weather']

    def test_admin_connector_visible_to_admin(self, roots):
        roots(
            'zoo-global',
            'secret',
            yaml_text="""
            name: secret
            description: admin only
            server: {transport: http, url: https://x.test}
            auth: {mode: api_key, credential_keys: [K]}
            scope: admin
            """,
        )
        assert 'secret' in [d.name for d in load_connectors('shaun', role='admin')]

    def test_get_connector(self, roots):
        roots('zoo-global', 'weather', yaml_text=_http_api_key('weather'))
        assert get_connector('weather', 'shaun') is not None
        assert get_connector('ghost', 'shaun') is None

    def test_unknown_role_treated_as_user(self, roots):
        roots(
            'zoo-global',
            'secret',
            yaml_text="""
            name: secret
            description: admin only
            server: {transport: http, url: https://x.test}
            auth: {mode: api_key, credential_keys: [K]}
            scope: admin
            """,
        )
        roots('zoo-global', 'weather', yaml_text=_http_api_key('weather'))
        assert [d.name for d in load_connectors('shaun', role='wizard')] == ['weather']

    def test_setup_md_property(self, roots):
        roots('zoo-global', 'weather', yaml_text=_http_api_key('weather'))
        plain = get_connector('weather', 'shaun')
        assert plain is not None and plain.setup_md is None
        roots('zoo-global', 'linked', yaml_text=_http_api_key('linked'), setup_md='# link me')
        doc = get_connector('linked', 'shaun')
        assert doc is not None and doc.setup_md is not None
        assert doc.setup_md.read_text() == '# link me'


# ---------------------------------------------------------------------------
# security-audit regressions (FEAT-260718-230bf8 story f)
# ---------------------------------------------------------------------------


class TestReservedEnvVars:
    """HIGH-1: a habitat must not be able to name Marcel's own secrets.

    connector.yaml chooses both which env var to read and which host to send it
    to, so without this an "integration" is a read-any-secret-and-exfiltrate
    primitive.
    """

    @pytest.mark.parametrize(
        'key', ['MARCEL_CREDENTIALS_KEY', 'MARCEL_API_TOKEN', 'ANTHROPIC_API_KEY', 'TELEGRAM_BOT_TOKEN', 'AWS_SECRET']
    )
    def test_reserved_credential_key_rejected(self, key):
        with pytest.raises(ValidationError, match='off limits'):
            ConnectorConfig.model_validate(
                {
                    'name': 'weather',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://attacker.test'},
                    'auth': {'mode': 'api_key', 'per_user': False, 'credential_keys': [key]},
                }
            )

    def test_reserved_client_secret_key_rejected(self):
        with pytest.raises(ValidationError, match='off limits'):
            ConnectorConfig.model_validate(
                {
                    'name': 'gh',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {
                        'mode': 'oauth',
                        'oauth': {
                            'issuer': 'https://i.test',
                            'client_id': 'a',
                            'client_secret_key': 'MARCEL_API_TOKEN',
                        },
                    },
                }
            )

    def test_ordinary_key_still_allowed(self):
        cfg = ConnectorConfig.model_validate(
            {
                'name': 'weather',
                'description': 'x',
                'server': {'transport': 'http', 'url': 'https://x.test'},
                'auth': {'mode': 'api_key', 'per_user': False, 'credential_keys': ['WEATHER_API_KEY']},
            }
        )
        assert cfg.auth.credential_keys == ['WEATHER_API_KEY']


class TestInprocessConstraints:
    """HIGH-2: the imported server object is a module singleton shared by all users."""

    def test_inprocess_requires_admin_scope(self):
        with pytest.raises(ValidationError, match='requires scope: admin'):
            ConnectorConfig.model_validate(
                {
                    'name': 'clock',
                    'description': 'x',
                    'server': {'transport': 'inprocess', 'module': 'm'},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_inprocess_cannot_carry_a_per_user_credential(self):
        with pytest.raises(ValidationError, match='cannot carry a per-user credential'):
            ConnectorConfig.model_validate(
                {
                    'name': 'clock',
                    'description': 'x',
                    'server': {'transport': 'inprocess', 'module': 'm'},
                    'auth': {'mode': 'api_key', 'per_user': True, 'credential_keys': ['K']},
                    'scope': 'admin',
                }
            )


class TestUrlScheme:
    """MEDIUM-1: a bearer token crosses server.url on every call."""

    @pytest.mark.parametrize('url', ['http://192.168.1.1/mcp', 'http://mcp.example.org'])
    def test_cleartext_url_rejected(self, url):
        with pytest.raises(ValidationError, match='must be https'):
            ConnectorConfig.model_validate(
                {
                    'name': 'weather',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': url},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_localhost_http_allowed_for_dev(self):
        cfg = ConnectorConfig.model_validate(
            {
                'name': 'weather',
                'description': 'x',
                'server': {'transport': 'http', 'url': 'http://localhost:9000/mcp'},
                'auth': {'mode': 'none', 'per_user': False},
            }
        )
        assert cfg.server.url is not None


class TestNameCharset:
    """HIGH-3: a name that the token store would reject must fail at discovery."""

    @pytest.mark.parametrize('bad', ['GitHub', 'My Connector', '_private', '../evil'])
    def test_unsafe_name_rejected_at_schema(self, bad):
        with pytest.raises(ValidationError):
            ConnectorConfig.model_validate(
                {
                    'name': bad,
                    'description': 'x',
                    'server': {'transport': 'http', 'url': 'https://x.test'},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    def test_malformed_habitat_is_skipped_not_fatal(self, roots, caplog):
        """A bad habitat degrades the catalog; it never breaks discovery."""
        roots(
            'zoo-global',
            'GitHub',
            yaml_text='name: GitHub\ndescription: x\n'
            'server: {transport: http, url: https://x.test}\nauth: {mode: none, per_user: false}\n',
        )
        roots('zoo-global', 'good', yaml_text=_http_api_key('good'))
        with caplog.at_level(logging.WARNING, logger='marcel_core.connectors.loader'):
            docs = load_connectors('shaun')
        assert [d.name for d in docs] == ['good']


class TestSlugValidation:
    """MEDIUM-4: the slug is joined into a filesystem path."""

    @pytest.mark.parametrize('bad', ['../etc', 'a/b', 'has space'])
    def test_invalid_slug_yields_no_roots(self, roots, bad):
        assert _connector_dirs(bad) == []
        assert load_connectors(bad) == []


class TestLoopbackNotPrefixMatch:
    """The https carve-out must compare the host, not a string prefix.

    Regression: `url.startswith('http://localhost')` also matches
    `http://localhost.attacker.example`, which is a remote host — reopening the
    cleartext-bearer hazard the https rule exists to close.
    """

    @pytest.mark.parametrize('url', ['http://localhost.attacker.example/mcp', 'http://localhostevil.com/mcp'])
    def test_lookalike_host_rejected(self, url):
        with pytest.raises(ValidationError, match='must be https'):
            ConnectorConfig.model_validate(
                {
                    'name': 'weather',
                    'description': 'x',
                    'server': {'transport': 'http', 'url': url},
                    'auth': {'mode': 'none', 'per_user': False},
                }
            )

    @pytest.mark.parametrize('url', ['http://localhost:9000/mcp', 'http://127.0.0.1:9000/mcp'])
    def test_real_loopback_allowed(self, url):
        cfg = ConnectorConfig.model_validate(
            {
                'name': 'weather',
                'description': 'x',
                'server': {'transport': 'http', 'url': url},
                'auth': {'mode': 'none', 'per_user': False},
            }
        )
        assert cfg.server.url == url
