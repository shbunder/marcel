"""Tests for api/chat.py — WebSocket chat endpoint."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from marcel_core.channels.websocket import WebSocketAdapter
from marcel_core.harness.runner import RunFinished, RunStarted, TextDelta, ToolCallCompleted, ToolCallStarted
from marcel_core.main import app
from marcel_core.rate_limit import _reset_ws_bucket_for_tests
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _fresh_ws_bucket():
    """Every test starts with a full rate-limit bucket.

    The bucket is a process-wide singleton keyed by user slug; without a
    reset, enough websocket tests in one session drain the 'shaun' key and a
    later turn gets an 'error: rate limit' frame instead of 'done' — which a
    receive-until-done loop waits on forever (order-dependent hang).
    """
    _reset_ws_bucket_for_tests()
    yield
    _reset_ws_bucket_for_tests()


def _mock_stream(monkeypatch, tokens: list[str], cost: float | None = None):
    """Patch stream_turn in chat to yield synthetic events."""

    async def fake_stream(*args, **kwargs):
        yield RunStarted(conversation_id='test-conv')
        for t in tokens:
            yield TextDelta(text=t)
        yield RunFinished(total_cost_usd=cost)

    monkeypatch.setattr('marcel_core.api.chat.stream_turn', fake_stream)
    monkeypatch.setattr(
        'marcel_core.api.chat.extract_and_save_memories',
        lambda *a, **k: asyncio.sleep(0),
    )


class TestChatWebSocket:
    def test_new_conversation_sends_started(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['Hello'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            started = json.loads(ws.receive_text())
            assert started['type'] == 'started'
            assert started['conversation'] is not None

    def test_streams_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['Hi', ' there'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            msg_start = json.loads(ws.receive_text())
            assert msg_start['type'] == 'text_message_start'
            delta1 = json.loads(ws.receive_text())
            assert delta1['type'] == 'token'
            assert delta1['text'] == 'Hi'
            delta2 = json.loads(ws.receive_text())
            assert delta2['text'] == ' there'
            msg_end = json.loads(ws.receive_text())
            assert msg_end['type'] == 'text_message_end'
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'

    def test_done_includes_cost(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['ok'], cost=0.03)
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            ws.receive_text()  # text_message_start
            ws.receive_text()  # token
            ws.receive_text()  # text_message_end
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'
            assert done['cost_usd'] == 0.03

    def test_empty_message_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, [])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': '  ', 'user': 'shaun'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'

    def test_invalid_user_slug_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['ok'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello', 'user': 'INVALID USER!'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'slug' in msg['message'].lower() or 'user' in msg['message'].lower()

    def test_continue_existing_conversation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.memory.conversation import ensure_channel

        ensure_channel('shaun', 'websocket')
        conv_id = 'websocket-default'
        _mock_stream(monkeypatch, ['reply'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': conv_id}))
            # No 'started' message when conversation already exists
            first = json.loads(ws.receive_text())
            assert first['type'] == 'text_message_start'

    def test_tool_call_events_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def fake_stream(*args, **kwargs):
            yield RunStarted(conversation_id='test-conv')
            yield ToolCallStarted(tool_call_id='tc-1', tool_name='bash')
            yield ToolCallCompleted(tool_call_id='tc-1', tool_name='bash', result='done')
            yield TextDelta(text='I ran bash')
            yield RunFinished()

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', fake_stream)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'run bash', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            tc_start = json.loads(ws.receive_text())
            assert tc_start['type'] == 'tool_call_start'
            assert tc_start['tool_name'] == 'bash'
            tc_end = json.loads(ws.receive_text())
            assert tc_end['type'] == 'tool_call_end'
            tc_result = json.loads(ws.receive_text())
            assert tc_result['type'] == 'tool_call_result'
            msg_start = json.loads(ws.receive_text())
            assert msg_start['type'] == 'text_message_start'
            token = json.loads(ws.receive_text())
            assert token['type'] == 'token'
            msg_end = json.loads(ws.receive_text())
            assert msg_end['type'] == 'text_message_end'
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'

    def test_stream_exception_sends_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def boom(*args, **kwargs):
            yield RunStarted(conversation_id='x')
            raise RuntimeError('kaboom')
            yield TextDelta(text='unreachable')  # intentionally unreachable — the raise above ends the stream

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', boom)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            err = json.loads(ws.receive_text())
            assert err['type'] == 'error'

    def test_invalid_api_token_rejected(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_api_token', 'real-secret-token')
        _mock_stream(monkeypatch, ['hi'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'token': 'wrong-token'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'token' in msg['message'].lower() or 'invalid' in msg['message'].lower()

    def test_valid_api_token_accepted(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_api_token', 'correct-token')
        _mock_stream(monkeypatch, ['hi'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'token': 'correct-token'}))
            started = json.loads(ws.receive_text())
            assert started['type'] == 'started'

    def test_no_user_returns_error(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_default_user', '')
        _mock_stream(monkeypatch, [])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello'}))  # no user field
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'user' in msg['message'].lower()


# ---------------------------------------------------------------------------
# Slash-prefix wiring (ISSUE-6a38cd) — /fast, /power, /<skillname>
# ---------------------------------------------------------------------------


class TestChatSlashPrefixes:
    def test_power_prefix_returns_reject_message_without_model_call(self, tmp_path, monkeypatch):
        """``/power ...`` → reject text streamed back, stream_turn never invoked."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        stream_called = False

        async def never_stream(*args, **kwargs):
            nonlocal stream_called
            stream_called = True
            yield RunStarted(conversation_id='x')

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', never_stream)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': '/power give me opus', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            msg_start = json.loads(ws.receive_text())
            assert msg_start['type'] == 'text_message_start'
            token = json.loads(ws.receive_text())
            assert token['type'] == 'token'
            assert 'power' in token['text'].lower()
            assert 'reserved' in token['text'].lower()
            msg_end = json.loads(ws.receive_text())
            assert msg_end['type'] == 'text_message_end'
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'

        assert stream_called is False

    def test_fast_prefix_passes_tier_and_cleaned_text_to_stream(self, tmp_path, monkeypatch):
        """``/fast hello`` → stream_turn receives turn_plan with USER_PREFIX tier and 'hello'."""
        from marcel_core.harness.model_chain import Tier
        from marcel_core.harness.turn_router import TierSource

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        captured: dict = {}

        async def fake_stream(user_slug, channel, user_text, conversation_id, **kwargs):
            captured['user_text'] = user_text
            captured['turn_plan'] = kwargs.get('turn_plan')
            yield RunStarted(conversation_id=conversation_id)
            yield TextDelta(text='ok')
            yield RunFinished()

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', fake_stream)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': '/fast hello', 'user': 'shaun', 'conversation': None}))
            # Drain until done
            while True:
                msg = json.loads(ws.receive_text())
                if msg['type'] == 'done':
                    break

        plan = captured['turn_plan']
        assert plan is not None
        assert plan.tier is Tier.FAST
        assert plan.source is TierSource.USER_PREFIX
        assert plan.cleaned_text == 'hello'


# ---------------------------------------------------------------------------
# Telegram initData authentication
# ---------------------------------------------------------------------------


class _LinkedTelegramChannel:
    def resolve_user_slug(self, external_id: str) -> str | None:
        return 'alice' if external_id == '42' else None


class TestChatTelegramAuth:
    def test_invalid_init_data_closes_connection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.api.chat.verify_telegram_init_data', lambda _: None)
        _mock_stream(monkeypatch, ['hi'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'initData': 'tampered-blob'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'telegram' in msg['message'].lower()
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 4001

    def test_unlinked_telegram_user_closes_connection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.api.chat.verify_telegram_init_data', lambda _: {'id': 999})
        monkeypatch.setattr('marcel_core.api.chat.get_channel', lambda name: None)
        _mock_stream(monkeypatch, ['hi'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'initData': 'valid-but-unlinked'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'not linked' in msg['message'].lower()
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 4001

    def test_linked_telegram_user_forces_slug(self, tmp_path, monkeypatch):
        """The slug resolved from initData wins over any client-supplied 'user' field."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.api.chat.verify_telegram_init_data', lambda _: {'id': 42})
        monkeypatch.setattr('marcel_core.api.chat.get_channel', lambda name: _LinkedTelegramChannel())
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        captured: dict = {}

        async def fake_stream(user_slug, channel, user_text, conversation_id, **kwargs):
            captured['user_slug'] = user_slug
            yield RunStarted(conversation_id=conversation_id)
            yield TextDelta(text='ok')
            yield RunFinished()

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', fake_stream)

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'initData': 'valid', 'user': 'mallory', 'conversation': None}))
            while True:
                msg = json.loads(ws.receive_text())
                assert msg['type'] != 'error'
                if msg['type'] == 'done':
                    break

        assert captured['user_slug'] == 'alice'


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestChatRateLimit:
    def test_over_limit_message_rejected_but_connection_survives(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['ok'])

        class _FlakyBucket:
            def __init__(self) -> None:
                self.calls = 0

            def allow(self, key: str) -> bool:
                self.calls += 1
                return self.calls > 1  # first message over limit, then recovers

        bucket = _FlakyBucket()
        monkeypatch.setattr('marcel_core.api.chat.get_ws_bucket', lambda: bucket)

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'rate limit' in msg['message'].lower()

            # The connection stays open — the next message goes through.
            ws.send_text(json.dumps({'text': 'hi again', 'user': 'shaun', 'conversation': None}))
            started = json.loads(ws.receive_text())
            assert started['type'] == 'started'


# ---------------------------------------------------------------------------
# Connection resilience
# ---------------------------------------------------------------------------


class TestChatResilience:
    def test_second_turn_skips_reauthentication(self, tmp_path, monkeypatch):
        """One connection, two turns — auth happens once, both turns stream."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['ok'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            for text in ('first turn', 'second turn'):
                ws.send_text(json.dumps({'text': text, 'user': 'shaun', 'conversation': None}))
                types = []
                while True:
                    msg = json.loads(ws.receive_text())
                    types.append(msg['type'])
                    if msg['type'] == 'done':
                        break
                assert 'error' not in types
                assert 'token' in types

    def test_error_frame_send_failure_swallowed(self, tmp_path, monkeypatch):
        """When the turn fails AND the error frame can't be sent, the connection survives."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        class _BrokenErrorAdapter(WebSocketAdapter):
            async def send_error(self, message: str) -> None:
                raise RuntimeError('error sink broken')

        monkeypatch.setattr('marcel_core.api.chat.WebSocketAdapter', _BrokenErrorAdapter)

        calls = {'n': 0}

        async def flaky_stream(*args, **kwargs):
            calls['n'] += 1
            yield RunStarted(conversation_id='c')
            if calls['n'] == 1:
                raise RuntimeError('boom')
            yield TextDelta(text='recovered')
            yield RunFinished()

        monkeypatch.setattr('marcel_core.api.chat.stream_turn', flaky_stream)

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            started = json.loads(ws.receive_text())
            assert started['type'] == 'started'

            # First turn died and its error frame could not be sent; the loop
            # continues — a second turn on the same connection still works.
            ws.send_text(json.dumps({'text': 'again', 'user': 'shaun', 'conversation': 'cli-default'}))
            types = []
            while True:
                msg = json.loads(ws.receive_text())
                types.append(msg['type'])
                if msg['type'] == 'done':
                    break
            assert 'error' not in types
            assert 'token' in types

    def test_non_json_payload_closes_connection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _mock_stream(monkeypatch, ['ok'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text('this is not json')
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
