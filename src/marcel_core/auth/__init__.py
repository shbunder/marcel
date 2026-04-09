"""Authentication and input validation helpers."""

import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl

from marcel_core.config import settings

_SLUG_RE = re.compile(r'^[a-z0-9_-]+$')

# Maximum age (seconds) for Telegram initData before it's considered stale.
_INIT_DATA_MAX_AGE = 300


def valid_user_slug(slug: str) -> bool:
    """Return True if *slug* matches ``[a-z0-9_-]+``."""
    return bool(_SLUG_RE.match(slug))


def verify_api_token(token: str) -> bool:
    """Check *token* against the ``MARCEL_API_TOKEN`` environment variable.

    Returns ``True`` if no token is configured (open access) or if the
    provided token matches.
    """
    expected = settings.marcel_api_token
    if not expected:
        return True
    return token == expected


def verify_telegram_init_data(init_data: str) -> dict[str, Any] | None:
    """Validate a Telegram Mini App ``initData`` string.

    Uses the HMAC-SHA256 algorithm specified by Telegram:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Returns the parsed ``user`` dict on success, ``None`` on failure.
    """
    bot_token = settings.telegram_bot_token
    if not bot_token:
        return None

    pairs = parse_qsl(init_data, keep_blank_values=True)
    received_hash = ''
    filtered: list[tuple[str, str]] = []
    auth_date = 0
    user_json = ''

    for key, value in pairs:
        if key == 'hash':
            received_hash = value
        else:
            filtered.append((key, value))
            if key == 'auth_date':
                try:
                    auth_date = int(value)
                except ValueError:
                    return None
            elif key == 'user':
                user_json = value

    if not received_hash or not auth_date or not user_json:
        return None

    # Check staleness
    if abs(time.time() - auth_date) > _INIT_DATA_MAX_AGE:
        return None

    # Build data-check string: sorted key=value pairs joined by \n
    filtered.sort(key=lambda p: p[0])
    data_check_string = '\n'.join(f'{k}={v}' for k, v in filtered)

    # HMAC chain: secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None

    try:
        return json.loads(user_json)
    except (json.JSONDecodeError, TypeError):
        return None
