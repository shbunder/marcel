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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        return self


class ConnectorConfig(BaseModel):
    """A parsed, validated ``connector.yaml``.

    ``name`` must equal the connector's directory name (checked by the loader,
    which knows the directory); everything else is validated here.
    """

    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    server: ServerSpec
    auth: AuthSpec
    tools: list[str] = Field(default_factory=list)  # optional allowlist; empty ⇒ all
    scope: Scope = Scope.ALL
    discovery: Discovery = Discovery.DEFERRED
    default_enabled: DefaultEnabled = DefaultEnabled.ALL
