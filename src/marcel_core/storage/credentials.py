"""Encrypted credential storage using Fernet symmetric encryption.

Credentials are stored as encrypted files in ``data/users/{slug}/credentials.enc``.
The encryption key is derived from the ``MARCEL_CREDENTIALS_KEY`` environment variable.

If ``MARCEL_CREDENTIALS_KEY`` is not set, credentials fall back to plaintext
``credentials.env`` files (legacy behavior) with a warning logged on first access.
"""

import base64
import hashlib
import logging
import pathlib

from cryptography.fernet import Fernet, InvalidToken

from marcel_core.config import settings

from ._atomic import atomic_write
from ._root import data_root

log = logging.getLogger(__name__)

_warned_plaintext = False


def _derive_key() -> bytes | None:
    """Derive a Fernet key from MARCEL_CREDENTIALS_KEY, or return None if unset."""
    passphrase = settings.marcel_credentials_key
    if not passphrase:
        return None
    # Derive a 32-byte key via SHA-256, then base64-encode for Fernet
    raw = hashlib.sha256(passphrase.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _enc_path(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug / 'credentials.enc'


def _plain_path(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug / 'credentials.env'


def _parse_env(text: str) -> dict[str, str]:
    """Parse key=value pairs from a string, ignoring comments and blanks."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        result[key.strip()] = value.strip()
    return result


def _serialize_env(creds: dict[str, str]) -> str:
    """Serialize credentials dict to key=value text."""
    return '\n'.join(f'{k}={v}' for k, v in sorted(creds.items())) + '\n'


def load_credentials(slug: str) -> dict[str, str]:
    """Load credentials for *slug*, preferring encrypted file over plaintext.

    Returns an empty dict if no credential file exists.
    """
    global _warned_plaintext
    key = _derive_key()

    # Try encrypted file first
    enc = _enc_path(slug)
    if enc.exists() and key is not None:
        f = Fernet(key)
        try:
            plaintext = f.decrypt(enc.read_bytes()).decode('utf-8')
            return _parse_env(plaintext)
        except InvalidToken:
            log.error('Failed to decrypt credentials for user %s — wrong MARCEL_CREDENTIALS_KEY?', slug)
            return {}

    # Fall back to plaintext
    plain = _plain_path(slug)
    if plain.exists():
        if not _warned_plaintext:
            _warned_plaintext = True
            if key is not None:
                log.info('Migrating plaintext credentials to encrypted storage for user %s', slug)
            else:
                log.warning('Using plaintext credentials (set MARCEL_CREDENTIALS_KEY to enable encryption)')
        creds = _parse_env(plain.read_text(encoding='utf-8'))

        # Auto-migrate: if we have a key and plaintext exists, encrypt and remove plaintext
        if key is not None and creds:
            save_credentials(slug, creds)
            plain.unlink()
            log.info('Migrated credentials for user %s to encrypted storage', slug)

        return creds

    return {}


def save_credentials(slug: str, creds: dict[str, str]) -> None:
    """Save credentials for *slug*, encrypted if MARCEL_CREDENTIALS_KEY is set."""
    key = _derive_key()
    plaintext = _serialize_env(creds)

    if key is not None:
        f = Fernet(key)
        encrypted = f.encrypt(plaintext.encode('utf-8'))
        path = _enc_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypted)
        # Set restrictive permissions
        os.chmod(path, 0o600)
    else:
        path = _plain_path(slug)
        atomic_write(path, plaintext)
        os.chmod(path, 0o600)
