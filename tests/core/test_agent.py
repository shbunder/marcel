"""Tests for agent loop — context building, streaming, sessions, and memory extraction."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock
from fastapi.testclient import TestClient

from marcel_core.agent.context import build_system_prompt
from marcel_core.agent.memory_extract import extract_and_save_memories
from marcel_core.agent.memory_select import _parse_selection, select_relevant_memories
from marcel_core.agent.runner import TurnResult, stream_response
from marcel_core.agent.sessions import ActiveSession, SessionManager
from marcel_core.main import app
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _agen(*items):
    """Yield items as an async generator — used to mock receive_response."""
    for item in items:
        yield item


def _stream_event(text: str) -> StreamEvent:
    return StreamEvent(
        uuid='u',
        session_id='s',
        event={'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}},
    )


def _assistant_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model='claude-sonnet-4-6')


def _result_message(cost: float = 0.01, turns: int = 1) -> ResultMessage:
    return ResultMessage(
        subtype='success',
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=turns,
        session_id='test-session',
        total_cost_usd=cost,
    )


def _make_mock_session(response_items: list) -> ActiveSession:
    """Create a mock ActiveSession whose client.query + receive_response work."""
    client = AsyncMock()
    client.query = AsyncMock()
    client.receive_response = MagicMock(return_value=_agen(*response_items))
    client.disconnect = AsyncMock()
    return ActiveSession(
        client=client,
        user_slug='shaun',
        conversation_id='test-conv',
        channel='cli',
    )


async def _collect_stream(agen):
    """Collect text tokens and the final TurnResult from stream_response."""
    tokens = []
    result = None
    async for item in agen:
        if isinstance(item, TurnResult):
            result = item
        else:
            tokens.append(item)
    return tokens, result


# ---------------------------------------------------------------------------
# context.py — build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_includes_user_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli')
        assert 'shaun' in prompt
        assert 'Marcel' in prompt

    def test_includes_profile_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_user_profile

        save_user_profile('shaun', '# Shaun\nLoves coffee.')
        prompt = build_system_prompt('shaun', 'cli')
        assert 'Loves coffee.' in prompt

    def test_empty_profile_shows_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli')
        assert 'no profile information yet' in prompt

    def test_includes_memory_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file, update_memory_index

        save_memory_file('shaun', 'calendar', '# Calendar\nPrefers mornings.')
        update_memory_index('shaun', 'calendar.md', 'calendar facts')
        prompt = build_system_prompt('shaun', 'cli')
        assert 'Prefers mornings.' in prompt

    def test_no_memory_section_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli')
        assert '## Memory' not in prompt

    def test_no_conversation_section(self, tmp_path, monkeypatch):
        """System prompt no longer includes conversation history (SDK manages it)."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli')
        assert '## Recent conversation' not in prompt

    def test_channel_hint_cli(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli')
        assert 'cli' in prompt
        assert 'markdown' in prompt.lower()

    def test_channel_hint_telegram(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'telegram')
        assert 'telegram' in prompt.lower()
        assert 'MarkdownV2' in prompt


# ---------------------------------------------------------------------------
# sessions.py — SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    def test_get_or_create_creates_new(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager()

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:
            mock_instance = AsyncMock()
            mock_instance.connect = AsyncMock()
            MockClient.return_value = mock_instance

            session = asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            assert session.user_slug == 'shaun'
            assert session.conversation_id == 'conv-1'
            assert mgr.active_count == 1
            mock_instance.connect.assert_awaited_once()

    def test_get_or_create_reuses_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager()

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:
            mock_instance = AsyncMock()
            mock_instance.connect = AsyncMock()
            MockClient.return_value = mock_instance

            s1 = asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            s2 = asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            assert s1 is s2
            assert mgr.active_count == 1
            # connect() only called once — session was reused
            assert mock_instance.connect.await_count == 1

    def test_disconnect_removes_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager()

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:
            mock_instance = AsyncMock()
            mock_instance.connect = AsyncMock()
            mock_instance.disconnect = AsyncMock()
            MockClient.return_value = mock_instance

            asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            assert mgr.active_count == 1
            asyncio.run(mgr.disconnect('shaun', 'conv-1'))
            assert mgr.active_count == 0
            mock_instance.disconnect.assert_awaited_once()

    def test_reset_user_disconnects_all_user_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager()

        call_count = 0

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:

            def make_client(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                m = AsyncMock()
                m.connect = AsyncMock()
                m.disconnect = AsyncMock()
                return m

            MockClient.side_effect = make_client

            asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            asyncio.run(mgr.get_or_create('shaun', 'conv-2', 'cli'))
            asyncio.run(mgr.get_or_create('alice', 'conv-3', 'cli'))
            assert mgr.active_count == 3

            asyncio.run(mgr.reset_user('shaun'))
            assert mgr.active_count == 1  # alice's session remains

    def test_cleanup_idle_removes_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager(idle_timeout=0.1)  # 100ms

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:
            mock_instance = AsyncMock()
            mock_instance.connect = AsyncMock()
            mock_instance.disconnect = AsyncMock()
            MockClient.return_value = mock_instance

            asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            assert mgr.active_count == 1

            # Simulate passage of time
            for s in mgr._sessions.values():
                s.last_active = time.monotonic() - 1.0

            removed = asyncio.run(mgr.cleanup_idle())
            assert removed == 1
            assert mgr.active_count == 0

    def test_disconnect_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mgr = SessionManager()

        call_count = 0

        with patch('marcel_core.agent.sessions.ClaudeSDKClient') as MockClient:

            def make_client(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                m = AsyncMock()
                m.connect = AsyncMock()
                m.disconnect = AsyncMock()
                return m

            MockClient.side_effect = make_client

            asyncio.run(mgr.get_or_create('shaun', 'conv-1', 'cli'))
            asyncio.run(mgr.get_or_create('alice', 'conv-2', 'cli'))
            assert mgr.active_count == 2

            asyncio.run(mgr.disconnect_all())
            assert mgr.active_count == 0


# ---------------------------------------------------------------------------
# runner.py — stream_response (with mocked SessionManager)
# ---------------------------------------------------------------------------


class TestStreamResponse:
    def test_yields_tokens_from_stream_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event('Hello'),
                _stream_event(' world'),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        tokens, result = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert tokens == ['Hello', ' world']
        assert result is not None
        assert result.total_cost_usd == 0.01

    def test_falls_back_to_assistant_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _assistant_message('Fallback text'),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        tokens, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert tokens == ['Fallback text']

    def test_ignores_assistant_message_when_stream_events_received(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event('streamed'),
                _assistant_message('full text'),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        tokens, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert tokens == ['streamed']

    def test_skips_empty_text_deltas(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event(''),
                _stream_event('real'),
                _stream_event(''),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        tokens, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert tokens == ['real']

    def test_result_message_captures_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event('ok'),
                _result_message(cost=0.05, turns=3),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, result = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert result is not None
        assert result.total_cost_usd == 0.05
        assert result.num_turns == 3
        assert result.session_id == 'test-session'
        assert result.is_error is False


# ---------------------------------------------------------------------------
# memory_extract.py — agent-based extraction
# ---------------------------------------------------------------------------


class TestExtractAndSaveMemories:
    def test_calls_query_with_correct_options(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        captured_kwargs = {}

        async def capture_query(**kwargs):
            captured_kwargs.update(kwargs)
            yield ResultMessage(
                subtype='success',
                duration_ms=50,
                duration_api_ms=40,
                is_error=False,
                num_turns=1,
                session_id='extract-1',
                total_cost_usd=0.001,
            )

        monkeypatch.setattr(claude_agent_sdk, 'query', capture_query)
        asyncio.run(extract_and_save_memories('shaun', 'I like tea', 'Noted!', 'conv-1'))

        # Verify correct model and tools preset.
        opts = captured_kwargs.get('options')
        assert opts is not None
        assert opts.model == 'claude-haiku-4-5-20251001'
        assert opts.tools == {'type': 'preset', 'preset': 'claude_code'}
        assert opts.max_turns == 3
        # CWD should be user's memory dir.
        expected_cwd = str(tmp_path / 'users' / 'shaun' / 'memory')
        assert opts.cwd == expected_cwd
        # Prompt should include the user/assistant text.
        assert 'I like tea' in captured_kwargs.get('prompt', '')
        assert 'Noted!' in captured_kwargs.get('prompt', '')

    def test_includes_manifest_in_system_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'prefs', '---\nname: prefs\ntype: preference\n---\nLikes tea.')

        captured_kwargs = {}

        async def capture_query(**kwargs):
            captured_kwargs.update(kwargs)
            yield ResultMessage(
                subtype='success',
                duration_ms=50,
                duration_api_ms=40,
                is_error=False,
                num_turns=1,
                session_id='s',
                total_cost_usd=0.001,
            )

        monkeypatch.setattr(claude_agent_sdk, 'query', capture_query)
        asyncio.run(extract_and_save_memories('shaun', 'hello', 'hi', 'conv-1'))

        # System prompt should contain the existing memory manifest.
        system = captured_kwargs['options'].system_prompt
        assert 'prefs.md' in system
        assert '[preference]' in system

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def boom(**_):
            raise RuntimeError('api down')
            yield  # make it an async generator  # noqa: RET503

        monkeypatch.setattr(claude_agent_sdk, 'query', boom)
        # Should not raise
        asyncio.run(extract_and_save_memories('shaun', 'x', 'y', 'conv-1'))


# ---------------------------------------------------------------------------
# api/chat.py — WebSocket (mocked runner)
# ---------------------------------------------------------------------------


class TestChatWebSocket:
    def _mock_stream(self, monkeypatch, tokens: list[str], cost: float | None = None):
        async def fake_stream(*args, **kwargs):
            for t in tokens:
                yield t
            yield TurnResult(total_cost_usd=cost)

        monkeypatch.setattr('marcel_core.api.chat.stream_response', fake_stream)
        monkeypatch.setattr(
            'marcel_core.api.chat.extract_and_save_memories',
            lambda *a, **k: asyncio.sleep(0),
        )

    def test_new_conversation_sends_started(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        self._mock_stream(monkeypatch, ['Hi'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello', 'user': 'shaun', 'conversation': None}))
            started = json.loads(ws.receive_text())
            assert started['type'] == 'started'
            assert started['conversation'] is not None

    def test_streams_tokens_and_done(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        self._mock_stream(monkeypatch, ['Hello', ' there'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            token1 = json.loads(ws.receive_text())
            token2 = json.loads(ws.receive_text())
            done = json.loads(ws.receive_text())
            assert token1 == {'type': 'token', 'text': 'Hello'}
            assert token2 == {'type': 'token', 'text': ' there'}
            assert done['type'] == 'done'

    def test_done_includes_cost_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        self._mock_stream(monkeypatch, ['ok'], cost=0.05)
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            ws.receive_text()  # token
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'
            assert done['cost_usd'] == 0.05

    def test_empty_message_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        self._mock_stream(monkeypatch, [])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': '  ', 'user': 'shaun'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'

    def test_continue_existing_conversation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import new_conversation

        conv_id = new_conversation('shaun', 'cli')
        self._mock_stream(monkeypatch, ['reply'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': conv_id}))
            # No 'started' message since conversation already exists
            token = json.loads(ws.receive_text())
            assert token['type'] == 'token'
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'


# ---------------------------------------------------------------------------
# memory_select.py — relevance selection
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_parses_json_array(self):
        assert _parse_selection('["calendar.md", "family.md"]') == ['calendar.md', 'family.md']

    def test_parses_empty_array(self):
        assert _parse_selection('[]') == []

    def test_handles_code_fences(self):
        response = '```json\n["calendar.md"]\n```'
        assert _parse_selection(response) == ['calendar.md']

    def test_handles_non_json(self):
        assert _parse_selection('I think calendar.md is relevant') == []

    def test_filters_non_strings(self):
        assert _parse_selection('[42, "valid.md", null]') == ['valid.md']


class TestSelectRelevantMemories:
    def test_returns_empty_for_no_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = asyncio.run(select_relevant_memories('shaun', 'hello'))
        assert result == []

    def test_loads_all_for_small_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'calendar', '---\nname: cal\ntype: schedule\n---\nDentist Friday.')
        save_memory_file('shaun', 'prefs', '---\nname: prefs\ntype: preference\n---\nLikes tea.')

        result = asyncio.run(select_relevant_memories('shaun', 'what do I like?'))
        assert len(result) == 2
        contents = [c for _, c in result]
        assert any('Dentist Friday.' in c for c in contents)
        assert any('Likes tea.' in c for c in contents)

    def test_includes_household_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'personal', 'My stuff.')
        save_memory_file('_household', 'wifi', '---\ntype: household\n---\nPassword: 12345.')

        result = asyncio.run(select_relevant_memories('shaun', 'wifi password'))
        assert len(result) == 2
        contents = [c for _, c in result]
        assert any('Password: 12345.' in c for c in contents)

    def test_excludes_household_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'personal', 'My stuff.')
        save_memory_file('_household', 'wifi', 'Password: 12345.')

        result = asyncio.run(select_relevant_memories('shaun', 'wifi', include_household=False))
        assert len(result) == 1
        assert 'My stuff.' in result[0][1]
