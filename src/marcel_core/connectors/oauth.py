"""OAuth 2.1 + PKCE linking for connectors (FEAT-260718-230bf8).

The linking dance, host-side:

1. :meth:`ConnectorOAuth.start_link` mints a PKCE verifier/challenge and an
   unguessable ``state``, remembers them **bound to the user slug + connector**
   in a short-lived, single-use pending store, and returns the provider's
   authorization URL for delivery over the user's channel.
2. The provider redirects the browser to ``<public base>/connectors/callback``.
   :meth:`ConnectorOAuth.complete_link` looks the ``state`` up (consuming it),
   exchanges the code at the token endpoint **with the verifier**, and persists
   the tokens encrypted under that user's connector directory.
3. :meth:`ConnectorOAuth.refresh` satisfies the story-(b) ``TokenRefresher``
   protocol, so expiry is handled transparently at call time.

Security shape: ``state`` is 256 bits of ``secrets`` randomness, single-use, and
TTL-bound — it is the whole CSRF/cross-user defence, so the slug is read from
the *server-side* pending record, never from the callback query. PKCE is S256.
The verifier lives only in memory (never on disk). Codes, verifiers and tokens
are never logged. Endpoint discovery and the token exchange are HTTPS-only.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from marcel_core.connectors.models import AuthMode, ConnectorConfig, OAuthSpec
from marcel_core.connectors.tokens import StoredTokens, TokenStore

log = logging.getLogger(__name__)

# Resolves (connector_name, slug) -> ConnectorConfig | None. Injected so the
# OAuth flow never reaches into the loader's scoping rules itself.
ConnectorLookup = Callable[[str, str], ConnectorConfig | None]

_CALLBACK_PATH = '/connectors/callback'
_PENDING_TTL_SECONDS = 600  # 10 minutes, the usual authorization-code window
_HTTP_TIMEOUT = 15.0


class OAuthError(Exception):
    """A linking step failed in a way the user should see a readable message for."""


@dataclass
class _PendingLink:
    """Server-side half of an in-flight link — the slug binding lives here."""

    slug: str
    connector: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)

    def is_expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.created_at > _PENDING_TTL_SECONDS


class PendingLinkStore:
    """In-memory, single-use, TTL-bound store of in-flight links.

    Deliberately not persisted: the PKCE verifier is a short-lived secret and
    must never sit on disk. A restart mid-link simply means the user re-runs the
    link (a readable "expired, try again"), which is the safer failure.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingLink] = {}

    def put(self, state: str, link: _PendingLink) -> None:
        self._prune()
        self._pending[state] = link

    def consume(self, state: str) -> _PendingLink | None:
        """Pop a pending link by state; ``None`` if unknown or expired (single use)."""
        self._prune()
        link = self._pending.pop(state, None)
        if link is None or link.is_expired():
            return None
        return link

    def _prune(self) -> None:
        for state in [s for s, link in self._pending.items() if link.is_expired()]:
            self._pending.pop(state, None)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(64))  # ~86 chars, inside the 43–128 range
    challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    return verifier, challenge


def public_base_url() -> str:
    """The configured public base URL, validated. Raises :class:`OAuthError` if unusable."""
    from marcel_core.config import settings

    base = (settings.marcel_public_url or '').rstrip('/')
    if not base:
        raise OAuthError(
            'Connector linking is not configured yet — the server needs its public address '
            '(MARCEL_PUBLIC_URL) set before accounts can be connected.'
        )
    if not base.startswith('https://') and not base.startswith('http://localhost'):
        raise OAuthError(
            'Connector linking needs a secure public address — MARCEL_PUBLIC_URL must be https '
            '(http is only allowed for localhost during development).'
        )
    return base


def redirect_uri() -> str:
    """The single redirect URI Marcel registers and expects back."""
    return f'{public_base_url()}{_CALLBACK_PATH}'


@dataclass
class _Endpoints:
    authorization_endpoint: str
    token_endpoint: str


class ConnectorOAuth:
    """Drives the OAuth 2.1 + PKCE link/refresh/unlink flows for connectors."""

    def __init__(
        self,
        store: TokenStore | None = None,
        pending: PendingLinkStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._store = store or TokenStore()
        self._pending = pending or PendingLinkStore()
        self._http = http_client

    # -- discovery ------------------------------------------------------------

    async def _discover(self, issuer: str) -> _Endpoints:
        """Resolve the authorization/token endpoints from the issuer (RFC 8414)."""
        base = issuer.rstrip('/')
        if not base.startswith('https://'):
            raise OAuthError('This connector is misconfigured — its login service must use https.')
        async with self._client() as client:
            for suffix in ('/.well-known/oauth-authorization-server', '/.well-known/openid-configuration'):
                try:
                    resp = await client.get(f'{base}{suffix}')
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                data = resp.json()
                auth_ep, token_ep = data.get('authorization_endpoint'), data.get('token_endpoint')
                if auth_ep and token_ep:
                    return _Endpoints(authorization_endpoint=auth_ep, token_endpoint=token_ep)
        raise OAuthError("Could not reach this connector's login service — try again in a moment.")

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return _NonClosing(self._http)  # type: ignore[return-value]
        return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)

    # -- link -----------------------------------------------------------------

    async def start_link(self, config: ConnectorConfig, slug: str) -> str:
        """Begin linking *slug* to *config*; returns the URL for them to open."""
        oauth = self._require_oauth(config)
        target = redirect_uri()
        endpoints = await self._discover(oauth.issuer)
        verifier, challenge = generate_pkce()
        state = _b64url(secrets.token_bytes(32))
        self._pending.put(
            state,
            _PendingLink(slug=slug, connector=config.name, code_verifier=verifier, redirect_uri=target),
        )
        params = {
            'response_type': 'code',
            'client_id': oauth.client_id,
            'redirect_uri': target,
            'state': state,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
        }
        if oauth.scopes:
            params['scope'] = ' '.join(oauth.scopes)
        log.info('connectors: started OAuth link for %s/%s', slug, config.name)
        return f'{endpoints.authorization_endpoint}?{urlencode(params)}'

    async def complete_link(self, config_for: ConnectorLookup, code: str, state: str) -> tuple[str, str]:
        """Finish linking from the callback. Returns ``(slug, connector)``.

        The slug comes from the server-side pending record keyed by ``state`` —
        never from the request — so a forged callback cannot bind another user.
        """
        link = self._pending.consume(state)
        if link is None:
            raise OAuthError('That connection link has expired or was already used — please start again.')
        config = config_for(link.connector, link.slug)
        if config is None:
            raise OAuthError('That connector is no longer available.')
        oauth = self._require_oauth(config)
        endpoints = await self._discover(oauth.issuer)

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': link.redirect_uri,
            'client_id': oauth.client_id,
            'code_verifier': link.code_verifier,
        }
        tokens = await self._token_request(endpoints.token_endpoint, data, oauth.client_secret_key)
        self._store.store(link.slug, link.connector, tokens)
        _audit('connector_link', link.slug, link.connector)
        log.info('connectors: linked %s/%s', link.slug, link.connector)
        return link.slug, link.connector

    # -- refresh (satisfies the story-b TokenRefresher protocol) ---------------

    async def refresh(self, config: ConnectorConfig, refresh_token: str) -> StoredTokens:
        oauth = self._require_oauth(config)
        endpoints = await self._discover(oauth.issuer)
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': oauth.client_id,
        }
        return await self._token_request(endpoints.token_endpoint, data, oauth.client_secret_key)

    # -- unlink ---------------------------------------------------------------

    def unlink(self, slug: str, connector: str) -> bool:
        """Forget a user's tokens for *connector*. Returns whether anything was removed."""
        removed = self._store.delete(slug, connector)
        if removed:
            _audit('connector_unlink', slug, connector)
            log.info('connectors: unlinked %s/%s', slug, connector)
        return removed

    # -- internals ------------------------------------------------------------

    async def _token_request(self, token_endpoint: str, data: dict[str, str], secret_key: str | None) -> StoredTokens:
        if secret_key:
            secret = os.environ.get(secret_key)
            if not secret:
                raise OAuthError('This connector is missing its server-side credential — an admin needs to set it up.')
            data = {**data, 'client_secret': secret}
        async with self._client() as client:
            try:
                resp = await client.post(token_endpoint, data=data, headers={'Accept': 'application/json'})
            except httpx.HTTPError:
                raise OAuthError('Could not reach the login service to finish connecting — please try again.') from None
        if resp.status_code != 200:
            # Never echo the body: it can carry the code or a token.
            log.warning('connectors: token endpoint returned %s', resp.status_code)
            raise OAuthError('The login service rejected the connection — please try linking again.')
        payload = resp.json()
        access = payload.get('access_token')
        if not access:
            raise OAuthError('The login service did not return an access token — please try linking again.')
        expires_in = payload.get('expires_in')
        return StoredTokens(
            access_token=access,
            refresh_token=payload.get('refresh_token'),
            expires_at=(time.time() + float(expires_in)) if expires_in else None,
            token_type=payload.get('token_type') or 'Bearer',
            scope=payload.get('scope'),
        )

    @staticmethod
    def _require_oauth(config: ConnectorConfig) -> OAuthSpec:
        if config.auth.mode is not AuthMode.OAUTH or config.auth.oauth is None:
            raise OAuthError(f'{config.name!r} does not use account linking.')
        return config.auth.oauth


class _NonClosing:
    """Wrap an injected client so ``async with`` does not close the caller's client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        return None


def _audit(event: str, slug: str, connector: str) -> None:
    """Append a link/unlink line to the audit log. Never records token material."""
    from marcel_core.storage.approvals import append_audit

    append_audit(
        {
            'type': event,
            'user': slug,
            'connector': connector,
            'at': datetime.now(UTC).isoformat(),
        }
    )
