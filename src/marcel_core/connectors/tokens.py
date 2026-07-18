"""Per-user connector token store (FEAT-260718-230bf8).

OAuth access/refresh tokens live encrypted under
``<data>/users/<slug>/connectors/<name>/tokens.enc`` (Fernet, 0600). Unlike the
credential vault, tokens have **no plaintext fallback** — if
``MARCEL_CREDENTIALS_KEY`` is unset the store refuses to persist, because a
bearer token must never sit unencrypted on disk (NFR1).

Path components are validated against a strict allowlist before any filesystem
op, so a hostile slug or connector name can never traverse out of the user's
directory (data-boundaries rule).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from marcel_core.storage._crypto import derive_fernet_key
from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)

# One path segment: lowercase alnum start, then alnum plus `._-`. No slashes, no
# `..`, no leading dot — rejects traversal and hidden-file tricks.
_SAFE_COMPONENT = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
# Default clock skew: refresh a token this many seconds *before* it actually
# expires, so an in-flight request never races the expiry.
_EXPIRY_SKEW_SECONDS = 60


class TokenStoreError(Exception):
    """Raised when tokens cannot be persisted (e.g. no encryption key)."""


def _safe(value: str, kind: str) -> str:
    if not _SAFE_COMPONENT.match(value):
        raise ValueError(f'unsafe {kind} {value!r} — must match {_SAFE_COMPONENT.pattern}')
    return value


@dataclass
class StoredTokens:
    """A user's tokens for one connector."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # unix seconds; None ⇒ never expires
    token_type: str = 'Bearer'
    scope: str | None = None

    def is_expired(self, skew_seconds: int = _EXPIRY_SKEW_SECONDS, *, now: float | None = None) -> bool:
        """Whether the access token is expired (or within ``skew_seconds`` of it)."""
        if self.expires_at is None:
            return False
        current = time.time() if now is None else now
        return current >= self.expires_at - skew_seconds

    def to_json(self) -> str:
        return json.dumps(
            {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expires_at': self.expires_at,
                'token_type': self.token_type,
                'scope': self.scope,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> StoredTokens:
        data = json.loads(text)
        return cls(
            access_token=data['access_token'],
            refresh_token=data.get('refresh_token'),
            expires_at=data.get('expires_at'),
            token_type=data.get('token_type', 'Bearer'),
            scope=data.get('scope'),
        )


class TokenStore:
    """Loads, stores and deletes per-user connector tokens (Fernet at rest)."""

    def _path(self, slug: str, connector: str) -> Path:
        return data_root() / 'users' / _safe(slug, 'slug') / 'connectors' / _safe(connector, 'connector') / 'tokens.enc'

    def load(self, slug: str, connector: str) -> StoredTokens | None:
        """Return the user's tokens for *connector*, or ``None`` if unlinked."""
        key = derive_fernet_key()
        path = self._path(slug, connector)
        if key is None or not path.exists():
            return None
        try:
            plaintext = Fernet(key).decrypt(path.read_bytes()).decode('utf-8')
        except InvalidToken:
            # Never log token bytes — just that decryption failed.
            log.error('connectors: cannot decrypt tokens for %s/%s (wrong MARCEL_CREDENTIALS_KEY?)', slug, connector)
            return None
        return StoredTokens.from_json(plaintext)

    def store(self, slug: str, connector: str, tokens: StoredTokens) -> None:
        """Persist *tokens* encrypted (0600). Raises if no key is configured."""
        key = derive_fernet_key()
        if key is None:
            raise TokenStoreError('MARCEL_CREDENTIALS_KEY is not set — connector tokens cannot be stored unencrypted')
        path = self._path(slug, connector)
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = Fernet(key).encrypt(tokens.to_json().encode('utf-8'))
        path.write_bytes(encrypted)
        os.chmod(path, 0o600)

    def delete(self, slug: str, connector: str) -> bool:
        """Remove the user's tokens for *connector* (the unlink flow). Returns whether a file was removed."""
        path = self._path(slug, connector)
        if path.exists():
            path.unlink()
            return True
        return False
