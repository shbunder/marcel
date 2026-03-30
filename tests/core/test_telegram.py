"""Tests for ISSUE-011 & ISSUE-018: Telegram channel integration.

Covers bot.py (escape helper), sessions.py (state management, coder mode),
and webhook.py (routing, /start, /code, /done, /new commands, coder dispatch).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import respx
from fastapi.testclient import TestClient
from httpx import Response

from marcel_core.agent.coder import CoderResult
from marcel_core.main import app
from marcel_core.storage import _root
from marcel_core.telegram import sessions
from marcel_core.telegram.bot import escape_markdown_v2

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


# ---------------------------------------------------------------------------
# sessions.py — coder mode state (ISSUE-018)
# ---------------------------------------------------------------------------


class TestCoderModeState:
    def test_default_mode_is_assistant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        assert sessions.get_mode(123) == 'assistant'

    def test_enter_and_exit_coder_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.enter_coder_mode(123, coder_session_id='sess-1')
        assert sessions.get_mode(123) == 'coder'
        assert sessions.get_coder_session_id(123) == 'sess-1'
        sessions.exit_coder_mode(123)
        assert sessions.get_mode(123) == 'assistant'
        assert sessions.get_coder_session_id(123) is None

    def test_reset_session_clears_everything(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.set_conversation_id(123, 'conv-1')
        sessions.enter_coder_mode(123, coder_session_id='sess-1')
        sessions.reset_session(123)
        assert sessions.get_mode(123) == 'assistant'
        assert sessions.get_coder_session_id(123) is None
        assert sessions.get_conversation_id(123) is None

    def test_set_coder_session_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.enter_coder_mode(123)
        sessions.set_coder_session_id(123, 'new-sess')
        assert sessions.get_coder_session_id(123) == 'new-sess'


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
        # Write a timestamp 7 hours ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        sessions._update_state(123, last_message_at=old_time)
        assert sessions.should_auto_new(123) is True

    def test_exactly_at_threshold_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # Write a timestamp exactly at the boundary (5h59m — below 6h)
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=59)).isoformat()
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
        assert sessions.get_mode(123) == 'assistant'


# ---------------------------------------------------------------------------
# webhook.py — /code, /done, /new commands (ISSUE-018)
# ---------------------------------------------------------------------------


def _mock_coder(monkeypatch, response: str = 'Done!', session_id: str = 'sess-1') -> None:
    """Mock run_coder_task to return a canned CoderResult."""

    async def fake_coder(prompt, *, resume_session_id=None, on_progress=None):
        return CoderResult(response=response, session_id=session_id)

    monkeypatch.setattr('marcel_core.telegram.webhook.run_coder_task', fake_coder)


class TestCoderWebhook:
    @respx.mock
    def test_code_command_enters_coder_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)
        _mock_coder(monkeypatch)

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/code add retry logic'))
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}
        # Auto-exits coder mode after task completes
        assert sessions.get_mode(555) == 'assistant'

    @respx.mock
    def test_code_without_prompt_shows_usage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)

        sent_payloads: list[dict] = []

        def capture(request):
            sent_payloads.append(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/code'))
        assert resp.status_code == 200
        assert any('Usage' in p.get('text', '') for p in sent_payloads)

    @respx.mock
    def test_done_command_exits_coder_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)
        sessions.enter_coder_mode(555)

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/done'))
        assert resp.status_code == 200
        assert sessions.get_mode(555) == 'assistant'

    @respx.mock
    def test_done_when_not_in_coder_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)

        sent_payloads: list[dict] = []

        def capture(request):
            sent_payloads.append(json.loads(request.content))
            return Response(200, json={'ok': True})

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(side_effect=capture)

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/done'))
        assert resp.status_code == 200
        assert any('Not in coder mode' in p.get('text', '') for p in sent_payloads)

    @respx.mock
    def test_new_command_resets_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)
        sessions.set_conversation_id(555, 'old-conv')
        sessions.enter_coder_mode(555)

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, '/new'))
        assert resp.status_code == 200
        assert sessions.get_mode(555) == 'assistant'
        assert sessions.get_conversation_id(555) is None

    @respx.mock
    def test_coder_mode_follow_up_routes_to_coder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
        sessions.link_user('shaun', 555)
        sessions.enter_coder_mode(555, coder_session_id='prev-sess')
        _mock_coder(monkeypatch, response='Follow-up done', session_id='sess-2')

        respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
            return_value=Response(200, json={'ok': True})
        )

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=_make_update(555, 'yes do it that way'))
        assert resp.status_code == 200
        # Auto-exits coder mode after task completes
        assert sessions.get_mode(555) == 'assistant'
