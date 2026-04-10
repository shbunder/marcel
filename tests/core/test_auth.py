"""Tests for auth/__init__.py — token verification and Telegram initData validation."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from marcel_core.auth import valid_user_slug, verify_api_token, verify_telegram_init_data
from marcel_core.config import settings

# ---------------------------------------------------------------------------
# valid_user_slug
# ---------------------------------------------------------------------------


class TestValidUserSlug:
    def test_accepts_lowercase_alpha(self):
        assert valid_user_slug('alice') is True

    def test_accepts_alphanumeric(self):
        assert valid_user_slug('user123') is True

    def test_accepts_hyphen(self):
        assert valid_user_slug('my-user') is True

    def test_accepts_underscore(self):
        assert valid_user_slug('my_user') is True

    def test_rejects_uppercase(self):
        assert valid_user_slug('Alice') is False

    def test_rejects_space(self):
        assert valid_user_slug('my user') is False

    def test_rejects_empty(self):
        assert valid_user_slug('') is False

    def test_rejects_slash(self):
        assert valid_user_slug('a/b') is False


# ---------------------------------------------------------------------------
# verify_api_token
# ---------------------------------------------------------------------------


class TestVerifyApiToken:
    def test_returns_true_when_no_token_configured(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_api_token', '')
        assert verify_api_token('anything') is True
        assert verify_api_token('') is True

    def test_accepts_correct_token(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_api_token', 'secret-token')
        assert verify_api_token('secret-token') is True

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_api_token', 'secret-token')
        assert verify_api_token('wrong') is False

    def test_rejects_empty_token_when_configured(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_api_token', 'secret-token')
        assert verify_api_token('') is False


# ---------------------------------------------------------------------------
# verify_telegram_init_data
# ---------------------------------------------------------------------------

_BOT_TOKEN = 'test-bot-token-123'


def _make_init_data(user: dict, *, offset: int = 0, bot_token: str = _BOT_TOKEN) -> str:
    """Build a valid Telegram initData string signed with *bot_token*."""
    auth_date = str(int(time.time()) + offset)
    user_json = json.dumps(user)
    pairs = [
        ('auth_date', auth_date),
        ('user', user_json),
    ]
    pairs_sorted = sorted(pairs, key=lambda p: p[0])
    data_check_string = '\n'.join(f'{k}={v}' for k, v in pairs_sorted)

    secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    all_pairs = pairs + [('hash', hash_value)]
    return urlencode(all_pairs)


class TestVerifyTelegramInitData:
    def test_returns_none_when_no_bot_token(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        assert verify_telegram_init_data('anything') is None

    def test_returns_user_on_valid_data(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        user = {'id': 123, 'first_name': 'Alice'}
        init_data = _make_init_data(user)
        result = verify_telegram_init_data(init_data)
        assert result is not None
        assert result['id'] == 123

    def test_returns_none_on_wrong_hash(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        user = {'id': 123}
        init_data = _make_init_data(user)
        # Tamper with the hash
        tampered = init_data.replace('hash=', 'hash=X')
        assert verify_telegram_init_data(tampered) is None

    def test_returns_none_on_stale_auth_date(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        user = {'id': 123}
        # offset by -10 minutes (600 seconds past threshold of 300)
        init_data = _make_init_data(user, offset=-600)
        assert verify_telegram_init_data(init_data) is None

    def test_returns_none_when_hash_missing(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        assert verify_telegram_init_data('auth_date=1234567890&user=%7B%7D') is None

    def test_returns_none_when_user_missing(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        assert verify_telegram_init_data('auth_date=1234567890&hash=abc') is None

    def test_returns_none_on_invalid_auth_date(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        assert verify_telegram_init_data('auth_date=notanumber&user={}&hash=abc') is None

    def test_returns_none_on_invalid_user_json(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        user = {'id': 123}
        # Build valid init_data but then mangle user JSON
        init_data = _make_init_data(user)
        # Replace user JSON with invalid JSON after signing — hash mismatch catches it
        # This tests the hash mismatch path rather than JSON decode path
        assert verify_telegram_init_data(init_data.replace('user=', 'user=INVALID')) is None

    def test_signed_with_different_bot_token_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        user = {'id': 123}
        init_data = _make_init_data(user, bot_token='different-token')
        assert verify_telegram_init_data(init_data) is None

    def test_returns_none_when_user_field_has_invalid_json_but_valid_hash(self, monkeypatch):
        """Cover the json.JSONDecodeError path in verify_telegram_init_data."""
        monkeypatch.setattr(settings, 'telegram_bot_token', _BOT_TOKEN)
        # Build initData where user field is not valid JSON but hash is correct
        auth_date = str(int(time.time()))
        user_value = 'not-valid-json'
        pairs = [('auth_date', auth_date), ('user', user_value)]
        pairs_sorted = sorted(pairs, key=lambda p: p[0])
        data_check_string = '\n'.join(f'{k}={v}' for k, v in pairs_sorted)
        secret_key = hmac.new(b'WebAppData', _BOT_TOKEN.encode(), hashlib.sha256).digest()
        hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        from urllib.parse import urlencode

        init_data = urlencode(pairs + [('hash', hash_value)])
        result = verify_telegram_init_data(init_data)
        assert result is None
