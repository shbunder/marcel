"""Authentication and input validation helpers."""

import os
import re

_SLUG_RE = re.compile(r'^[a-z0-9_-]+$')


def valid_user_slug(slug: str) -> bool:
    """Return True if *slug* matches ``[a-z0-9_-]+``."""
    return bool(_SLUG_RE.match(slug))


def verify_api_token(token: str) -> bool:
    """Check *token* against the ``MARCEL_API_TOKEN`` environment variable.

    Returns ``True`` if no token is configured (open access) or if the
    provided token matches.
    """
    expected = os.environ.get('MARCEL_API_TOKEN', '')
    if not expected:
        return True
    return token == expected
