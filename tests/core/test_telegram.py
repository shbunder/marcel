"""Tests for Telegram channel integration.

Covers bot.py (escape helpers), sessions.py (state management),
and webhook.py (routing, /start, /new commands).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import respx
from fastapi.testclient import TestClient
from httpx import Response

from marcel_core.channels.telegram import sessions
from marcel_core.channels.telegram.bot import escape_markdown_v2
from marcel_core.config import settings
from marcel_core.harness.runner import TextDelta
from marcel_core.main import app
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# bot.py — escape helpers
# ---------------------------------------------------------------------------


class TestEscapeMarkdownV2:
    """Legacy escape function — kept for backward compatibility."""

    def test_escapes_dot(self):
        assert escape_markdown_v2('hello.') == r'hello\.'

    def test_escapes_exclamation(self):
        assert escape_markdown_v2('hi!') == r'hi\!'

    def test_escapes_parens(self):
        assert escape_markdown_v2('(ok)') == r'\(ok\)'

    def test_escapes_underscore(self):
        assert escape_markdown_v2('some_name') == r'some\_name'

    def test_plain_text_unchanged(self):
        assert escape_markdown_v2('hello world') == 'hello world'

    def test_empty_string(self):
        assert escape_markdown_v2('') == ''

    def test_all_special_chars(self):
        result = escape_markdown_v2('_*[]()~`>#+-=|{}.!\\')
        assert '\\' in result
        # Every special char should be preceded by a backslash
        assert result.count('\\') >= len('_*[]()~`>#+-=|{}.!\\')


# ---------------------------------------------------------------------------
# sessions.py — state management
# ---------------------------------------------------------------------------


class TestGetUserSlug:
    def test_returns_slug_for_known_chat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('shaun', 123456789)
        assert sessions.get_user_slug(123456789) == 'shaun'

    def test_returns_slug_with_string_chat_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('shaun', '123456789')
        assert sessions.get_user_slug('123456789') == 'shaun'

    def test_returns_none_for_unknown_chat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('shaun', 123456789)
        assert sessions.get_user_slug(999) is None

    def test_handles_multiple_users(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('alice', 111)
        sessions.link_user('bob', 222)
        assert sessions.get_user_slug(111) == 'alice'
        assert sessions.get_user_slug(222) == 'bob'

    def test_returns_none_when_no_users_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        assert sessions.get_user_slug(123) is None

    def test_ignores_user_dir_without_telegram_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        (tmp_path / 'users' / 'shaun').mkdir(parents=True)
        assert sessions.get_user_slug(123) is None

    def test_link_user_writes_to_user_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('shaun', 556632386)
        import json as _json

        data = _json.loads((tmp_path / 'users' / 'shaun' / 'telegram.json').read_text())
        assert data['chat_id'] == '556632386'


class TestConversationState:
    def test_returns_none_when_no_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        assert sessions.get_conversation_id(999) is None

    def test_set_and_get_conversation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(123, '2026-03-29T12-00')
        assert sessions.get_conversation_id(123) == '2026-03-29T12-00'

    def test_persists_across_reloads(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(456, 'conv-abc')
        # Verify the file was actually written
        sessions_path = tmp_path / 'telegram' / 'sessions.json'
        data = json.loads(sessions_path.read_text())
        assert data['456']['conversation_id'] == 'conv-abc'

    def test_multiple_chats_independent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(1, 'conv-1')
        sessions.set_conversation_id(2, 'conv-2')
        assert sessions.get_conversation_id(1) == 'conv-1'
        assert sessions.get_conversation_id(2) == 'conv-2'

    def test_overwrites_existing_conversation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(1, 'old-conv')
        sessions.set_conversation_id(1, 'new-conv')
        assert sessions.get_conversation_id(1) == 'new-conv'


# ---------------------------------------------------------------------------
# webhook.py — HTTP endpoint
# ---------------------------------------------------------------------------


def _make_update(chat_id: int, text: str) -> dict:
    return {
        'update_id': 1,
        'message': {
            'message_id': 1,
            'chat': {'id': chat_id, 'type': 'private'},
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Shaun'},
            'text': text,
        },
    }


def _mock_stream(monkeypatch, tokens: list[str]) -> None:
    async def fake_stream(*args, **kwargs):
        for t in tokens:
            yield TextDelta(text=t)

    monkeypatch.setattr('marcel_core.channels.telegram.webhook.stream_turn', fake_stream)
    monkeypatch.setattr(
        'marcel_core.channels.telegram.webhook.extract_and_save_memories',
        lambda *a, **k: asyncio.sleep(0),
    )


_WEBHOOK_HEADERS = {'x-telegram-bot-api-secret-token': 'test-secret'}


class TestTelegramWebhook:
    def test_rejects_missing_webhook_secret(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # No TELEGRAM_WEBHOOK_SECRET set — should reject
        client = TestClient(app)
        resp = client.post('/telegram/webhook', json={'update_id': 1})
        assert resp.status_code == 503

    def test_ignores_update_without_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        client = TestClient(app)
        resp = client.post('/telegram/webhook', json={'update_id': 1}, headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ignored'}

    def test_ignores_empty_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        update = _make_update(123, '')
        update['message']['text'] = ''
        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=update, headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ignored'}

    @respx.mock
    def test_start_command_sends_chat_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')

        sent_payload = {}

        def capture(request):
            sent_payload.update(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(42, '/start'), headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
        assert '42' in sent_payload.get('text', '')

    @respx.mock
    def test_unlinked_chat_sends_explanation(self, tmp_path, monkeypatch):
        # No telegram.json written for any user — chat is unlinked
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')

        sent_payload = {}

        def capture(request):
            sent_payload.update(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(99, 'hello'), headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
        assert '99' in sent_payload.get('text', '')

    def test_rejects_bad_webhook_secret(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'my-secret')

        client = TestClient(app)
        resp = client.post(
            '/telegram/webhook',
            json=_make_update(1, 'hi'),
            headers={'x-telegram-bot-api-secret-token': 'wrong'},
        )
        assert resp.status_code == 403

    def test_accepts_correct_webhook_secret(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'my-secret')
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')

        with respx.mock:
            respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
                return_value=Response(200, json={'ok': True})
            )
            client = TestClient(app)
            resp = client.post(
                '/telegram/webhook',
                json=_make_update(1, 'hi'),
                headers={'x-telegram-bot-api-secret-token': 'my-secret'},
            )
        assert resp.status_code == 200

    @respx.mock
    def test_linked_chat_dispatches_to_agent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        sessions.link_user('shaun', 555)
        _mock_stream(monkeypatch, ['Hello', ' Shaun'])

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, 'hi'), headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}


# ---------------------------------------------------------------------------
# sessions.py — auto-new on inactivity
# ---------------------------------------------------------------------------


class TestAutoNewOnInactivity:
    def test_no_last_message_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        assert sessions.should_auto_new(123) is False

    def test_recent_message_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.touch_last_message(123)
        assert sessions.should_auto_new(123) is False

    def test_old_message_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write a timestamp 49 hours ago (AUTO_NEW_HOURS is 48)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        sessions._update_state(123, last_message_at=old_time)
        assert sessions.should_auto_new(123) is True

    def test_exactly_at_threshold_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write a timestamp just below 48h
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=47, minutes=59)).isoformat()
        sessions._update_state(123, last_message_at=recent_time)
        assert sessions.should_auto_new(123) is False


class TestLegacySessionMigration:
    def test_migrates_string_value_to_session_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write legacy format directly
        sessions_path = tmp_path / 'telegram' / 'sessions.json'
        sessions_path.parent.mkdir(parents=True, exist_ok=True)
        sessions_path.write_text(json.dumps({'123': 'old-conv-id'}))

        assert sessions.get_conversation_id(123) == 'old-conv-id'

    def test_migrates_dict_with_legacy_coder_fields(self, tmp_path, monkeypatch):
        """Legacy sessions with coder fields should be migrated cleanly."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions_path = tmp_path / 'telegram' / 'sessions.json'
        sessions_path.parent.mkdir(parents=True, exist_ok=True)
        sessions_path.write_text(
            json.dumps(
                {
                    '123': {
                        'conversation_id': 'conv-1',
                        'mode': 'coder',
                        'coder_session_id': 'sess-1',
                        'last_message_at': '2026-03-29T14:32:00',
                    }
                }
            )
        )
        assert sessions.get_conversation_id(123) == 'conv-1'


class TestClearAllSessions:
    def test_clears_conversation_ids(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(111, 'conv-a')
        sessions.set_conversation_id(222, 'conv-b')
        sessions.clear_all_sessions()
        assert sessions.get_conversation_id(111) is None
        assert sessions.get_conversation_id(222) is None

    def test_preserves_last_message_at(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(111, 'conv-a')
        sessions.touch_last_message(111)
        sessions.clear_all_sessions()
        # last_message_at should still be set
        state = sessions._get_state(111)
        assert state.last_message_at is not None

    def test_noop_when_no_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Should not raise
        sessions.clear_all_sessions()


class TestNewCommand:
    @respx.mock
    def test_new_command_resets_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        sessions.link_user('shaun', 555)
        sessions.set_conversation_id(555, 'old-conv')

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/new'), headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert sessions.get_conversation_id(555) is None


# ---------------------------------------------------------------------------
# conversations.py — _extract_assistant_message
# ---------------------------------------------------------------------------

from marcel_core.api.conversations import _extract_assistant_message


class TestExtractAssistantMessage:
    """Tests for assistant message extraction from conversation markdown."""

    _SIMPLE = (
        '# Conversation — 2026-04-03T11:00 (channel: telegram)\n\n'
        "**User:** What's the weather?\n\n"
        "**Marcel:** It's sunny and 20°C today.\n"
    )

    _MULTI_TURN = (
        '# Conversation — 2026-04-03T11:00 (channel: telegram)\n\n'
        "**User:** What's my salary?\n\n"
        "**Marcel:** Based on that single month, here's a **rough annual estimate**:\n\n"
        'Scenario — Calculation — Total\n'
        'Base (12x) — €3,264.60 x 12 — €39,175\n\n'
        '**User:** What should I not forget next week?\n\n'
        "**Marcel:** Here's what's on for next week (6-12 April)\n\n"
        '**Sunday 6 April**\n'
        '- 🏠 Kids return from Weekend VdB at 10:00\n\n'
        '**All week (6–20 April)**\n'
        '- 🐣 Paasvakantie — kids are off school\n'
    )

    def test_extracts_last_by_default(self):
        result = _extract_assistant_message(self._SIMPLE)
        assert result == "It's sunny and 20°C today."

    def test_does_not_truncate_at_bold_text(self):
        """The core bug: bold date headers like **Sunday 6 April** must not
        be mistaken for a turn marker."""
        result = _extract_assistant_message(self._MULTI_TURN)
        assert result is not None
        assert '**Sunday 6 April**' in result
        assert 'Kids return' in result
        assert 'Paasvakantie' in result

    def test_turn_0_returns_first_assistant_message(self):
        result = _extract_assistant_message(self._MULTI_TURN, turn=0)
        assert result is not None
        assert 'rough annual estimate' in result
        assert '€39,175' in result
        # Must NOT include the second response
        assert 'Sunday 6 April' not in result

    def test_turn_1_returns_second_assistant_message(self):
        result = _extract_assistant_message(self._MULTI_TURN, turn=1)
        assert result is not None
        assert 'next week' in result
        assert 'Sunday 6 April' in result

    def test_turn_out_of_range_returns_none(self):
        result = _extract_assistant_message(self._SIMPLE, turn=5)
        assert result is None

    def test_no_assistant_message_returns_none(self):
        raw = '# Conversation\n\n**User:** Hello?\n'
        assert _extract_assistant_message(raw) is None

    def test_last_message_in_multi_turn(self):
        """Default (turn=None) should return the last assistant message."""
        result = _extract_assistant_message(self._MULTI_TURN)
        assert result is not None
        assert 'next week' in result
        assert 'rough annual estimate' not in result
