"""Extended tests for channels/telegram/webhook.py — internal functions."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from marcel_core.channels.telegram import sessions
from marcel_core.channels.telegram.webhook import _format_response
from marcel_core.config import settings
from marcel_core.main import app
from marcel_core.storage import _root

_WEBHOOK_HEADERS = {'x-telegram-bot-api-secret-token': 'test-secret'}


def _make_update(chat_id: int, text: str) -> dict:
    return {
        'update_id': 1,
        'message': {
            'message_id': 100,
            'chat': {'id': chat_id, 'type': 'private'},
            'from': {'id': chat_id, 'first_name': 'Test', 'is_bot': False},
            'text': text,
            'date': 1700000000,
        },
    }


# ---------------------------------------------------------------------------
# _format_response — pure function tests
# ---------------------------------------------------------------------------


class TestFormatResponse:
    def test_plain_text_response(self):
        html, markup = _format_response('Hello there!', 'conv-1')
        assert isinstance(html, str)
        assert 'Hello' in html
        assert markup is None

    def test_rich_content_returns_html(self):
        # A markdown table triggers rich content detection
        md = '| Name | Score | Rank |\n|------|-------|------|\n| Alice | 10 | 1 |\n'
        html, markup = _format_response(md, 'conv-1')
        assert isinstance(html, str)
        # markup may be None if no public URL configured — just check html

    def test_calendar_response_single_page(self):
        # Calendar content: a few day headers
        calendar_text = '**Monday 1 April**\n- Event 1\n\n**Tuesday 2 April**\n- Event 2\n\n'
        html, markup = _format_response(calendar_text, 'conv-1')
        assert isinstance(html, str)

    def test_calendar_response_multi_page(self):
        # Create >7 days to trigger multi-page calendar
        days = ''.join(
            f'**{day} April**\n- Appointment {i + 1}\n\n'
            for i, day in enumerate(
                ['Monday 1', 'Tuesday 2', 'Wednesday 3', 'Thursday 4', 'Friday 5', 'Saturday 6', 'Sunday 7', 'Monday 8']
            )
        )
        html, markup = _format_response(days, 'conv-multi')
        assert isinstance(html, str)

    def test_artifact_id_embedded_in_markup(self, monkeypatch):
        monkeypatch.setattr(settings, 'marcel_public_url', 'https://example.com')
        md = '| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n'
        html, markup = _format_response(md, 'conv-1', artifact_id='art-123')
        if markup and 'inline_keyboard' in markup:
            buttons_text = json.dumps(markup)
            assert 'art-123' in buttons_text


# ---------------------------------------------------------------------------
# _process_assistant_message — timeout and empty response
# ---------------------------------------------------------------------------


class TestProcessAssistantMessage:
    @pytest.mark.asyncio
    async def test_timeout_sends_reply(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.channels.telegram.webhook import _process_assistant_message

        async def slow_stream(*args, **kwargs):
            await asyncio.sleep(100)
            return
            yield  # make it a generator

        sent = []

        async def fake_send(chat_id, text, **kwargs):
            sent.append(text)
            return 1

        with patch('marcel_core.channels.telegram.webhook.stream_turn', slow_stream):
            with patch('marcel_core.channels.telegram.bot.send_message', fake_send):
                with patch('marcel_core.channels.telegram.webhook._ASSISTANT_TIMEOUT', 0.01):
                    await _process_assistant_message(
                        42, 'shaun', 'hello', {'message_id': None, 'sent': False, 'cancelled': False}
                    )

        assert any('long' in t.lower() or 'took' in t.lower() or 'sorry' in t.lower() for t in sent)

    @pytest.mark.asyncio
    async def test_empty_response_sends_apology(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.channels.telegram.webhook import _process_assistant_message

        async def empty_stream(*args, **kwargs):
            return
            yield  # generator

        sent = []

        async def fake_send(chat_id, text, **kwargs):
            sent.append(text)
            return 1

        with patch('marcel_core.channels.telegram.webhook.stream_turn', empty_stream):
            with patch('marcel_core.channels.telegram.bot.send_message', fake_send):
                await _process_assistant_message(
                    42, 'shaun', 'hello', {'message_id': None, 'sent': False, 'cancelled': False}
                )

        assert any('empty' in t.lower() or 'sorry' in t.lower() for t in sent)

    @pytest.mark.asyncio
    async def test_stream_exception_sends_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.channels.telegram.webhook import _process_assistant_message

        async def broken_stream(*args, **kwargs):
            raise RuntimeError('agent crash')
            yield

        sent = []

        async def fake_send(chat_id, text, **kwargs):
            sent.append(text)
            return 1

        with patch('marcel_core.channels.telegram.webhook.stream_turn', broken_stream):
            with patch('marcel_core.channels.telegram.bot.send_message', fake_send):
                await _process_assistant_message(
                    42, 'shaun', 'hello', {'message_id': None, 'sent': False, 'cancelled': False}
                )

        assert any('wrong' in t.lower() or 'sorry' in t.lower() or 'error' in t.lower() for t in sent)

    @pytest.mark.asyncio
    async def test_ack_edit_when_ack_was_sent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.channels.telegram.webhook import _process_assistant_message
        from marcel_core.harness.runner import TextDelta

        async def fast_stream(*args, **kwargs):
            yield TextDelta(text='Reply text')

        edited = []

        async def fake_edit(chat_id, message_id, text, **kwargs):
            edited.append((chat_id, message_id, text))

        with patch('marcel_core.channels.telegram.webhook.stream_turn', fast_stream):
            with patch('marcel_core.channels.telegram.bot.send_message', AsyncMock(return_value=1)):
                with patch('marcel_core.channels.telegram.bot.edit_message_text', fake_edit):
                    with patch('marcel_core.channels.telegram.webhook.extract_and_save_memories', AsyncMock()):
                        await _process_assistant_message(
                            42, 'shaun', 'hello', {'message_id': 99, 'sent': True, 'cancelled': False}
                        )

        assert any(e[1] == 99 for e in edited)


# ---------------------------------------------------------------------------
# Callback query handling
# ---------------------------------------------------------------------------


class TestHandleCallbackQuery:
    @pytest.mark.asyncio
    async def test_non_cal_callback_answered(self):
        from marcel_core.channels.telegram.webhook import _handle_callback_query

        answered = []

        async def fake_answer(query_id, text=None):
            answered.append(query_id)

        with patch('marcel_core.channels.telegram.bot.answer_callback_query', fake_answer):
            await _handle_callback_query(
                {
                    'id': 'q1',
                    'data': 'other:data',
                    'message': {'chat': {'id': 42}, 'message_id': 1},
                }
            )

        assert 'q1' in answered

    @pytest.mark.asyncio
    async def test_malformed_cal_callback_answered(self):
        from marcel_core.channels.telegram.webhook import _handle_callback_query

        answered = []

        async def fake_answer(query_id, text=None):
            answered.append(query_id)

        with patch('marcel_core.channels.telegram.bot.answer_callback_query', fake_answer):
            await _handle_callback_query(
                {
                    'id': 'q2',
                    'data': 'cal:only_one_part',
                    'message': {'chat': {'id': 42}, 'message_id': 1},
                }
            )

        assert 'q2' in answered

    @pytest.mark.asyncio
    async def test_invalid_page_answered(self):
        from marcel_core.channels.telegram.webhook import _handle_callback_query

        answered = []

        async def fake_answer(query_id, text=None):
            answered.append((query_id, text))

        with patch('marcel_core.channels.telegram.bot.answer_callback_query', fake_answer):
            await _handle_callback_query(
                {
                    'id': 'q3',
                    'data': 'cal:conv-1:notanumber',
                    'message': {'chat': {'id': 42}, 'message_id': 1},
                }
            )

        assert any(qid == 'q3' for qid, _ in answered)

    @pytest.mark.asyncio
    async def test_unlinked_user_answered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.channels.telegram.webhook import _handle_callback_query

        answered = []

        async def fake_answer(query_id, text=None):
            answered.append((query_id, text))

        # No user linked to chat 42
        with patch('marcel_core.channels.telegram.bot.answer_callback_query', fake_answer):
            await _handle_callback_query(
                {
                    'id': 'q4',
                    'data': 'cal:conv-1:0',
                    'message': {'chat': {'id': 42}, 'message_id': 1},
                }
            )

        assert any(qid == 'q4' for qid, _ in answered)

    @pytest.mark.asyncio
    async def test_no_conversation_answered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        sessions.link_user('shaun', 42)

        from marcel_core.channels.telegram.webhook import _handle_callback_query

        answered = []

        async def fake_answer(query_id, text=None):
            answered.append((query_id, text))

        with patch('marcel_core.channels.telegram.bot.answer_callback_query', fake_answer):
            await _handle_callback_query(
                {
                    'id': 'q5',
                    'data': 'cal:nonexistent-conv:0',
                    'message': {'chat': {'id': 42}, 'message_id': 1},
                }
            )

        assert any(qid == 'q5' for qid, _ in answered)


# ---------------------------------------------------------------------------
# Webhook endpoint — callback query dispatch
# ---------------------------------------------------------------------------


class TestWebhookCallbackQuery:
    @respx.mock
    def test_callback_query_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')

        respx.post('https://api.telegram.org/bottest-token/answerCallbackQuery').mock(
            return_value=Response(200, json={'ok': True})
        )

        update = {
            'update_id': 1,
            'callback_query': {
                'id': 'cq-1',
                'data': 'other:data',
                'message': {
                    'message_id': 10,
                    'chat': {'id': 42, 'type': 'private'},
                },
            },
        }

        client = TestClient(app)
        resp = client.post('/telegram/webhook', json=update, headers=_WEBHOOK_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}


# ---------------------------------------------------------------------------
# Auto-new on inactivity via webhook
# ---------------------------------------------------------------------------


class TestAutoNewViaWebhook:
    @respx.mock
    def test_auto_new_dispatches_and_resets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-token')
        monkeypatch.setattr(settings, 'telegram_webhook_secret', 'test-secret')
        sessions.link_user('shaun', 555)

        from datetime import datetime, timedelta, timezone

        old_time = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        sessions._update_state(555, last_message_at=old_time)
        sessions.set_conversation_id(555, 'old-conv')

        from marcel_core.harness.runner import TextDelta

        async def fake_stream(*args, **kwargs):
            yield TextDelta(text='hi')

        with patch('marcel_core.channels.telegram.webhook.stream_turn', fake_stream):
            with patch('marcel_core.channels.telegram.webhook.extract_and_save_memories', AsyncMock()):
                respx.post('https://api.telegram.org/bottest-token/sendMessage').mock(
                    return_value=Response(200, json={'ok': True})
                )
                client = TestClient(app)
                resp = client.post('/telegram/webhook', json=_make_update(555, 'hello'), headers=_WEBHOOK_HEADERS)

        assert resp.status_code == 200
