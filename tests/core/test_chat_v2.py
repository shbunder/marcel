"""Tests for api/chat.py — WebSocket chat endpoint."""

import asyncio
import json

from fastapi.testclient import TestClient

from marcel_core.harness.runner import RunFinished, RunStarted, TextDelta, ToolCallCompleted, ToolCallStarted
from marcel_core.main import app
from marcel_core.storage import _root


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
            yield TextDelta(text='unreachable')  # noqa: unreachable

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
