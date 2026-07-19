"""``connector.yaml`` schema (FEAT-260718-230bf8).

A **connector** is an MCP server plus the per-user authentication layer needed
to use it (ADR-260718-231cad). This module is the declarative surface: a
:class:`ConnectorConfig` pydantic model with exhaustive, actionable validation
so a malformed habitat fails loud with a message a developer can act on, and
one bad connector never breaks discovery of the others (the isolation contract
lives in :mod:`marcel_core.connectors.loader`).

The kernel builds machinery here; migrating the existing toolkits onto it is a
later feature (FEAT-260718-c232d9), so nothing in this module imports a toolkit.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class Transport(str, Enum):
    """How Marcel reaches the connector's MCP server."""

    HTTP = 'http'  # shared upstream, per-request auth header
    STDIO = 'stdio'  # per-(connector, user) subprocess
    INPROCESS = 'inprocess'  # bundled FastMCP server in this venv


class AuthMode(str, Enum):
    OAUTH = 'oauth'  # OAuth 2.1 + PKCE, host-side token store
    API_KEY = 'api_key'  # per-user (or shared) key from the credential vault
    NONE = 'none'  # public server, no credential


class Scope(str, Enum):
    """Who may see the connector in their catalog."""

    ALL = 'all'
    ADMIN = 'admin'


class Discovery(str, Enum):
    """Whether the connector's tools sit in context eagerly or load on demand."""

    DEFERRED = 'deferred'  # behind ToolSearch / skill activation (default)
    EAGER = 'eager'  # hot-path opt-in — tools always present


class DefaultEnabled(str, Enum):
    """Which users a freshly-installed connector is enabled for by default.

    Reserved + validated here; the three-state lifecycle (ADR-260718-7addc8:
    seeding via FEAT-260718-210a5f, per-user enforcement via FEAT-260707-acb2b6)
    consumes it. Absent ⇒ ``all``.
    """

    ALL = 'all'
    ADMIN = 'admin'
    NONE = 'none'


# Env-var namespaces a habitat may never name. A connector.yaml chooses which
# environment variable to read for a shared credential and which URL to send it
# to, so without this an "integration" is a read-any-secret-and-exfiltrate
# primitive: `credential_keys: [MARCEL_CREDENTIALS_KEY]` would ship the Fernet
# master key — which decrypts every family member's vault and OAuth tokens — to
# whatever host `server.url` names. (Security audit, FEAT-260718-230bf8.)
RESERVED_ENV_PREFIXES = ('MARCEL_', 'ANTHROPIC_', 'OPENAI_', 'TELEGRAM_', 'AWS_')


# Hosts for which cleartext http is tolerated during development. Compared
# against the parsed *host*, never a string prefix: `http://localhost.attacker
# .example` starts with 'http://localhost' but is a remote host, so a prefix
# match would wave through exactly the cleartext-bearer hazard this rejects.
LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


def is_loopback_http(url: str) -> bool:
    """Whether *url* is plain http pointed at this machine."""
    if not url.startswith('http://'):
        return False
    return (urlparse(url).hostname or '') in LOOPBACK_HOSTS


def _reject_reserved_env(name: str, field: str) -> None:
    if name.upper().startswith(RESERVED_ENV_PREFIXES):
        raise ValueError(
            f"{field} may not reference {name!r} — Marcel's own secrets are off limits to habitats "
            f'(reserved prefixes: {", ".join(RESERVED_ENV_PREFIXES)})'
        )


class ServerSpec(BaseModel):
    """The ``server:`` block — transport plus its one required locator."""

    model_config = ConfigDict(extra='forbid')

    transport: Transport
    url: str | None = None  # required for http
    command: list[str] | None = None  # required for stdio (argv)
    module: str | None = None  # required for inprocess (import path of a FastMCP)

    @model_validator(mode='after')
    def _locator_matches_transport(self) -> ServerSpec:
        need = {
            Transport.HTTP: ('url', self.url),
            Transport.STDIO: ('command', self.command),
            Transport.INPROCESS: ('module', self.module),
        }[self.transport]
        field, value = need
        if not value:
            raise ValueError(f'server.transport {self.transport.value!r} requires server.{field!r}')
        # The other two locators must be absent, so a copy-paste error is loud.
        for other_field in ('url', 'command', 'module'):
            if other_field != field and getattr(self, other_field):
                raise ValueError(
                    f'server.transport {self.transport.value!r} uses server.{field!r}; '
                    f'remove the stray server.{other_field!r}'
                )
        # A bearer token crosses this URL on every call, and an http:// target
        # would put it in cleartext on the LAN (or point Marcel at the home
        # network as an SSRF primitive). Same localhost carve-out the public
        # base URL uses for development.
        if self.transport is Transport.HTTP and self.url is not None:
            if not self.url.startswith('https://') and not is_loopback_http(self.url):
                raise ValueError('server.url must be https (http is only allowed for localhost during development)')
        return self


class OAuthSpec(BaseModel):
    """OAuth 2.1 metadata — the issuer Marcel discovers endpoints from + scopes.

    ``client_id`` is the app registration Marcel authenticates as (there is no
    dynamic client registration — family members are not OAuth clients). It is
    not a secret, so it lives in the habitat. A *confidential* client also needs
    a secret: name the env var holding it in ``client_secret_key`` and keep the
    value in system config (``.env``), never in the habitat or a user's vault
    (data-boundaries). Public clients rely on PKCE alone and omit it.
    """

    model_config = ConfigDict(extra='forbid')

    issuer: str
    client_id: str
    client_secret_key: str | None = None  # env var name, not the secret
    scopes: list[str] = Field(default_factory=list)


class AuthSpec(BaseModel):
    """The ``auth:`` block — mode plus the fields that mode requires."""

    model_config = ConfigDict(extra='forbid')

    mode: AuthMode
    per_user: bool = True
    credential_keys: list[str] = Field(default_factory=list)  # api_key mode
    oauth: OAuthSpec | None = None  # oauth mode

    @model_validator(mode='after')
    def _fields_match_mode(self) -> AuthSpec:
        if self.mode is AuthMode.OAUTH:
            if self.oauth is None:
                raise ValueError("auth.mode 'oauth' requires an auth.oauth block (issuer + scopes)")
            if self.credential_keys:
                raise ValueError(
                    "auth.mode 'oauth' must not set auth.credential_keys — tokens come from the OAuth flow"
                )
        elif self.mode is AuthMode.API_KEY:
            if not self.credential_keys:
                raise ValueError("auth.mode 'api_key' requires auth.credential_keys (the vault key name(s))")
            if self.oauth is not None:
                raise ValueError("auth.mode 'api_key' must not set auth.oauth")
        else:  # NONE
            if self.credential_keys or self.oauth is not None:
                raise ValueError("auth.mode 'none' takes no credential_keys or oauth block")
            if self.per_user:
                raise ValueError("auth.mode 'none' cannot be per_user — there is no per-user credential")
        for key in self.credential_keys:
            _reject_reserved_env(key, 'auth.credential_keys')
        if self.oauth is not None and self.oauth.client_secret_key:
            _reject_reserved_env(self.oauth.client_secret_key, 'auth.oauth.client_secret_key')
        return self


class ConnectorScheduledJob(BaseModel):
    """One ``scheduled_jobs:`` entry from ``connector.yaml``.

    Field-compatible with the toolkit's ``ScheduledJobSpec`` (D1,
    FEAT-260718-c232d9): a park migrating from toolkit to connector keeps its
    job identity, cadence and notify policy — the scheduler materializes both
    through the same ``habitat:<name>`` template and stable job id.
    """

    model_config = ConfigDict(extra='forbid')

    name: str
    handler: str  # '<connector>.<tool>' — dispatched via the connector fallback
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str | None = None
    description: str = ''
    notify: str | None = None
    channel: str | None = None
    task: str | None = None
    system_prompt: str | None = None
    model: str | None = None

    @model_validator(mode='after')
    def _exactly_one_trigger(self) -> ConnectorScheduledJob:
        if (self.cron is None) == (self.interval_seconds is None):
            raise ValueError('scheduled_jobs entries need exactly one of cron or interval_seconds')
        return self


class ConnectorConfig(BaseModel):
    """A parsed, validated ``connector.yaml``.

    ``name`` must equal the connector's directory name (checked by the loader,
    which knows the directory); everything else is validated here.
    """

    model_config = ConfigDict(extra='forbid')

    # Same charset the token store accepts as a path component — validating it
    # here means a bad name fails at discovery (where it is isolated) instead of
    # raising later and taking down every user's agent build.
    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9][a-z0-9._-]*$')
    description: str = Field(min_length=1, max_length=1024)
    server: ServerSpec
    auth: AuthSpec
    tools: list[str] = Field(default_factory=list)  # optional allowlist; empty ⇒ all
    scope: Scope = Scope.ALL
    discovery: Discovery = Discovery.DEFERRED
    default_enabled: DefaultEnabled = DefaultEnabled.ALL
    scheduled_jobs: list[ConnectorScheduledJob] = Field(default_factory=list)

    # Where the habitat lives on disk — set by the loader after validation, so
    # a `server.module` ending in `.py` can be resolved relative to the park
    # (D3). Private: never part of the YAML surface.
    _connector_dir: object | None = PrivateAttr(default=None)

    @model_validator(mode='after')
    def _inprocess_carries_no_credential(self) -> ConnectorConfig:
        """An in-process server cannot hold a per-user credential.

        Python caches modules, so every user gets the *same* server object: the
        per-(connector, user) keying that keeps credential attribution honest
        does not apply, and the server has no way to know whose turn it is
        serving. ``auth: none`` is therefore structural, not advisory.

        ``scope`` is deliberately *not* constrained. The security audit
        (FEAT-260718-230bf8) originally paired this with ``scope: admin`` on the
        grounds that an inprocess server shares Marcel's memory — but that risk
        is about who may **install** a connector, and habitats are
        admin-installed either way. ``scope`` gates who may **use** one, which
        does not change what the code can reach: the server runs in Marcel's
        process whichever family member triggers it. Forcing admin-only bought
        no containment and would have kept a credential-free first-party server
        (news) away from the family it exists for. See ADR-260718-231cad,
        Amendment 2026-07-19b.
        """
        if self.server.transport is Transport.INPROCESS and self.auth.mode is not AuthMode.NONE:
            raise ValueError(
                "server.transport 'inprocess' cannot carry a per-user credential "
                '(the server object is shared across users); use stdio or http'
            )
        return self
