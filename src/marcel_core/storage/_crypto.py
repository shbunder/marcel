"""Shared Fernet key derivation — the single source of Marcel's at-rest key.

Both the credential vault (:mod:`marcel_core.storage.credentials`) and the
connector token store (:mod:`marcel_core.connectors.tokens`) encrypt with a
Fernet key derived here from ``MARCEL_CREDENTIALS_KEY``. Keeping the derivation
in one place means the two stores can never drift onto different keys.
"""

from __future__ import annotations

import base64
import hashlib

from marcel_core.config import settings


def derive_fernet_key() -> bytes | None:
    """Derive a Fernet key from ``MARCEL_CREDENTIALS_KEY``, or ``None`` if unset.

    SHA-256 of the passphrase, urlsafe-base64-encoded to the 32-byte form Fernet
    expects. ``None`` signals "no encryption configured" — callers decide the
    fallback (the credential vault degrades to plaintext with a warning; the
    connector token store refuses to persist, since tokens must never sit in
    plaintext).
    """
    passphrase = settings.marcel_credentials_key
    if not passphrase:
        return None
    raw = hashlib.sha256(passphrase.encode()).digest()
    return base64.urlsafe_b64encode(raw)
