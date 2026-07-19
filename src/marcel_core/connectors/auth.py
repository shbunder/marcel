"""ConnectorAuth — the single outbound-credential decision point (FEAT-260718-230bf8).

Every outbound credential a connector sends upstream is resolved **here and
nowhere else** (ADR-260718-231cad decision driver: "one decision point for which
credential goes out"). ``ConnectorAuth`` reads only from the per-user credential
vault (``api_key`` mode) or the per-user connector token store (``oauth`` mode);
it has no parameter through which a caller could hand it a Marcel-audience token
to forward. That structural shape *is* the token-passthrough prohibition the MCP
security BCP requires — a Marcel token can never leave via this class.

For ``oauth`` connectors the access token is refreshed transparently when it is
near expiry, via a pluggable :class:`TokenRefresher` (the real OAuth token
endpoint is wired by the linking story); the refreshed tokens are re-persisted.

An unlinked or unrefreshable user surfaces as :class:`ConnectorNotLinked`, whose
message is family-readable (NFR3) — callers turn it into a "not linked — ask to
set it up" reply, never a stack trace.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from marcel_core.connectors.models import AuthMode, ConnectorConfig
from marcel_core.connectors.tokens import StoredTokens, TokenStore

log = logging.getLogger(__name__)


class ConnectorNotLinked(Exception):
    """A connector needs per-user setup that this user has not completed."""


class TokenRefresher(Protocol):
    """Refreshes an OAuth access token from a refresh token.

    The real implementation (OAuth 2.1 token endpoint) is provided by the
    linking story; ConnectorAuth depends only on this narrow protocol so it is
    unit-testable without a provider.
    """

    async def refresh(self, config: ConnectorConfig, refresh_token: str) -> StoredTokens: ...


class ConnectorAuth:
    """Resolves the per-user outbound secret/headers for a connector."""

    def __init__(self, store: TokenStore | None = None, refresher: TokenRefresher | None = None) -> None:
        self._store = store or TokenStore()
        self._refresher = refresher

    def linkage_error(self, config: ConnectorConfig, slug: str) -> str | None:
        """Readable reason *slug* cannot use *config* yet, or ``None`` if usable.

        Synchronous on purpose: it only reads the vault, the environment and the
        token store, so the composition root can decide at agent-build time
        whether to expose a connector's tools or its "needs setup" stand-in.
        A token that is merely *expired but refreshable* counts as linked — the
        per-request auth flow refreshes it at call time.
        """
        auth = config.auth
        if auth.mode is AuthMode.NONE:
            return None
        if auth.mode is AuthMode.API_KEY:
            key_name = auth.credential_keys[0]
            if auth.per_user:
                from marcel_core.storage.credentials import load_credentials

                return None if load_credentials(slug).get(key_name) else self._msg_no_user_key(config, key_name)
            return None if os.environ.get(key_name) else self._msg_no_shared_key(config, key_name)

        tokens = self._store.load(slug, config.name)
        if tokens is None:
            return self._msg_not_linked(config)
        if tokens.is_expired() and not tokens.refresh_token:
            return self._msg_relink(config)
        return None

    @staticmethod
    def _msg_no_user_key(config: ConnectorConfig, key_name: str) -> str:
        return f'{config.name!r} is not set up for you yet — it needs your {key_name}.'

    @staticmethod
    def _msg_no_shared_key(config: ConnectorConfig, key_name: str) -> str:
        return f'{config.name!r} is not configured — the {key_name} is missing from the server settings.'

    @staticmethod
    def _msg_not_linked(config: ConnectorConfig) -> str:
        return f'{config.name!r} is not linked for you yet — you can connect it in settings.'

    @staticmethod
    def _msg_relink(config: ConnectorConfig) -> str:
        return f'{config.name!r} needs to be reconnected — its access has expired and cannot be refreshed.'

    async def resolve_secret(self, config: ConnectorConfig, slug: str) -> str | None:
        """Return the outbound bearer secret for *slug*, or ``None`` for ``auth none``.

        Raises :class:`ConnectorNotLinked` when the user has no usable credential.
        This is the *only* place an outbound connector secret is produced.
        """
        auth = config.auth
        if auth.mode is AuthMode.NONE:
            return None

        if auth.mode is AuthMode.API_KEY:
            key_name = auth.credential_keys[0]  # schema guarantees ≥1
            if auth.per_user:
                from marcel_core.storage.credentials import load_credentials

                value = load_credentials(slug).get(key_name)
                if not value:
                    raise ConnectorNotLinked(self._msg_no_user_key(config, key_name))
                return value
            # Shared key lives in system config (.env), not a user vault.
            value = os.environ.get(key_name)
            if not value:
                raise ConnectorNotLinked(self._msg_no_shared_key(config, key_name))
            return value

        # OAuth: load, refresh if near expiry, use the (possibly refreshed) access token.
        tokens = self._store.load(slug, config.name)
        if tokens is None:
            raise ConnectorNotLinked(self._msg_not_linked(config))
        if tokens.is_expired():
            tokens = await self._refresh(config, slug, tokens)
        return tokens.access_token

    async def _refresh(self, config: ConnectorConfig, slug: str, tokens: StoredTokens) -> StoredTokens:
        if not tokens.refresh_token or self._refresher is None:
            raise ConnectorNotLinked(self._msg_relink(config))
        refreshed = await self._refresher.refresh(config, tokens.refresh_token)
        # Providers may omit a new refresh token on refresh; keep the old one.
        if refreshed.refresh_token is None:
            refreshed.refresh_token = tokens.refresh_token
        self._store.store(slug, config.name, refreshed)
        log.info('connectors: refreshed access token for %s/%s', slug, config.name)
        return refreshed

    async def outbound_headers(self, config: ConnectorConfig, slug: str) -> dict[str, str]:
        """The auth header(s) to attach to an outbound request, or ``{}`` for ``auth none``.

        The value comes solely from :meth:`resolve_secret` — no Marcel token or
        channel credential is ever in scope here, so none can be forwarded.
        """
        secret = await self.resolve_secret(config, slug)
        if secret is None:
            return {}
        token_type = 'Bearer'
        if config.auth.mode is AuthMode.OAUTH:
            tokens = self._store.load(slug, config.name)
            if tokens is not None:
                token_type = tokens.token_type or 'Bearer'
        return {'Authorization': f'{token_type} {secret}'}
