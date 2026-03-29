"""Tests for ISSUE-011: Telegram channel integration.

Covers bot.py (escape helper), sessions.py (state management),
and webhook.py (routing, /start command, unlinked chats, message dispatch).
"""

import asyncio
import json

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from marcel_core.main import app
from marcel_core.storage import _root
from marcel_core.telegram.bot import escape_markdown_v2
from marcel_core.telegram import sessions


# ---------------------------------------------------------------------------
# bot.py — escape_markdown_v2
# ---------------------------------------------------------------------------


class TestEscapeMarkdownV2:
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
        assert data['456'] == 'conv-abc'

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
            yield t

    monkeypatch.setattr('marcel_core.telegram.webhook.stream_response', fake_stream)
    monkeypatch.setattr(
        'marcel_core.telegram.webhook.extract_and_save_memories',
        lambda *a, **k: asyncio.sleep(0),
    )


class TestTelegramWebhook:
    def test_ignores_update_without_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        client = TestClient(app)
        resp = client.post('/telegram/webhook', json={'update_id': 1})
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ignored'}

    def test_ignores_empty_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        update = _make_update(123, '')
        update['message']['text'] = ''
        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=update)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ignored'}

    @respx.mock
    def test_start_command_sends_chat_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')

        sent_payload = {}

        def capture(request):
            sent_payload.update(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(42, '/start'))
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
        assert '42' in sent_payload.get('text', '')

    @respx.mock
    def test_unlinked_chat_sends_explanation(self, tmp_path, monkeypatch):
        # No telegram.json written for any user — chat is unlinked
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')

        sent_payload = {}

        def capture(request):
            sent_payload.update(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(99, 'hello'))
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
        assert '99' in sent_payload.get('text', '')

    def test_rejects_bad_webhook_secret(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'my-secret')

        client = TestClient(app)
        resp = client.post(
            '/telegram/webhook',
            json=_make_update(1, 'hi'),
            headers={'x-telegram-bot-api-secret-token': 'wrong'},
        )
        assert resp.status_code == 403

    def test_accepts_correct_webhook_secret(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 'my-secret')
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')

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
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)
        _mock_stream(monkeypatch, ['Hello', ' Shaun'])

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, 'hi'))
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
