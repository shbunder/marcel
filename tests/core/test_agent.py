"""Tests for agent loop — context building, streaming, sessions, and memory extraction."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolResultBlock
from fastapi.testclient import TestClient

from marcel_core.agent.context import build_system_prompt
from marcel_core.agent.events import (
    RunFinished,
    RunStarted,
    TextMessageContent,
    TextMessageEnd,
    TextMessageStart,
    ToolCallEnd,
    ToolCallResult,
    ToolCallStart,
)
from marcel_core.agent.memory_extract import extract_and_save_memories
from marcel_core.agent.runner import stream_response
from marcel_core.agent.sessions import ActiveSession, SessionManager
from marcel_core.main import app
from marcel_core.memory.selector import _parse_selection, select_relevant_memories
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


def _tool_use_start_event(index: int, tool_id: str, tool_name: str) -> StreamEvent:
    return StreamEvent(
        uuid='u',
        session_id='s',
        event={
            'type': 'content_block_start',
            'index': index,
            'content_block': {'type': 'tool_use', 'id': tool_id, 'name': tool_name, 'input': {}},
        },
    )


def _tool_use_stop_event(index: int) -> StreamEvent:
    return StreamEvent(
        uuid='u',
        session_id='s',
        event={'type': 'content_block_stop', 'index': index},
    )


def _assistant_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model='claude-sonnet-4-6')


def _assistant_message_with_tool_result(
    tool_use_id: str,
    content: str = 'result',
    is_error: bool = False,
) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
        model='claude-sonnet-4-6',
    )


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
    """Collect text tokens and the final RunFinished from stream_response."""
    tokens: list[str] = []
    result: RunFinished | None = None
    all_events: list = []
    async for event in agen:
        all_events.append(event)
        if isinstance(event, TextMessageContent):
            tokens.append(event.text)
        elif isinstance(event, RunFinished):
            result = event
    return tokens, result, all_events


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

    def test_fallback_identity_when_no_marcelmd(self, tmp_path, monkeypatch):
        """When no MARCEL.md is found, fallback identity line is used."""
        import marcel_core.agent.context as ctx

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(ctx, '_load_marcelmd', lambda slug: '')
        prompt = build_system_prompt('shaun', 'cli')
        assert 'You are Marcel' in prompt

    def test_empty_memory_content_skipped(self, tmp_path, monkeypatch):
        """Memory files with whitespace-only content are excluded from prompt."""
        import marcel_core.agent.context as ctx
        from marcel_core.storage import save_memory_file

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        save_memory_file('shaun', 'empty_mem', '   \n   ')
        monkeypatch.setattr(ctx, '_load_marcelmd', lambda slug: '')
        prompt = build_system_prompt('shaun', 'cli')
        assert '## Memory' not in prompt

    def test_old_memory_gets_freshness_note(self, tmp_path, monkeypatch):
        """Old memories get a freshness note appended."""
        import os
        import time

        import marcel_core.agent.context as ctx
        from marcel_core.storage import save_memory_file

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        save_memory_file('shaun', 'old_mem', 'Old content here.')
        # Set mtime to 10 days ago
        mem_path = tmp_path / 'users' / 'shaun' / 'memory' / 'old_mem.md'
        old_time = time.time() - (10 * 86400)
        os.utime(mem_path, (old_time, old_time))
        monkeypatch.setattr(ctx, '_load_marcelmd', lambda slug: '')
        prompt = build_system_prompt('shaun', 'cli')
        assert 'days old' in prompt


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

        tokens, result, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
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

        tokens, _, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
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

        tokens, _, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
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

        tokens, _, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
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

        _, result, _ = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        assert result is not None
        assert result.total_cost_usd == 0.05
        assert result.num_turns == 3
        assert result.session_id == 'test-session'
        assert result.is_error is False

    def test_text_message_boundaries(self, tmp_path, monkeypatch):
        """TextMessageStart and TextMessageEnd wrap streamed text tokens."""
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

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        types = [type(e).__name__ for e in events]
        assert types == [
            'RunStarted',
            'TextMessageStart',
            'TextMessageContent',
            'TextMessageContent',
            'TextMessageEnd',
            'RunFinished',
        ]

    def test_emits_tool_call_events(self, tmp_path, monkeypatch):
        """Tool use content blocks produce ToolCallStart and ToolCallEnd events."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _tool_use_start_event(0, 'tool-1', 'Read'),
                _tool_use_stop_event(0),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        types = [type(e).__name__ for e in events]
        assert 'ToolCallStart' in types
        assert 'ToolCallEnd' in types

        start = next(e for e in events if isinstance(e, ToolCallStart))
        assert start.tool_call_id == 'tool-1'
        assert start.tool_name == 'Read'

        end = next(e for e in events if isinstance(e, ToolCallEnd))
        assert end.tool_call_id == 'tool-1'

    def test_tool_call_splits_text_messages(self, tmp_path, monkeypatch):
        """A tool call between text blocks produces two TextMessageStart/End pairs."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event('before'),
                _tool_use_start_event(1, 'tool-1', 'Edit'),
                _tool_use_stop_event(1),
                _stream_event('after'),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        types = [type(e).__name__ for e in events]
        assert types == [
            'RunStarted',
            'TextMessageStart',
            'TextMessageContent',
            'TextMessageEnd',
            'ToolCallStart',
            'ToolCallEnd',
            'TextMessageStart',
            'TextMessageContent',
            'TextMessageEnd',
            'RunFinished',
        ]

    def test_tool_result_from_assistant_message(self, tmp_path, monkeypatch):
        """ToolResultBlock in AssistantMessage produces ToolCallResult events."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _stream_event('text'),
                _assistant_message_with_tool_result('tool-1', content='file contents', is_error=False),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(results) == 1
        assert results[0].tool_call_id == 'tool-1'
        assert results[0].is_error is False
        assert 'file contents' in results[0].summary

    def test_fallback_path_has_text_boundaries(self, tmp_path, monkeypatch):
        """Fallback (no stream events) still emits TextMessageStart/End."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session(
            [
                _assistant_message('Fallback'),
                _result_message(),
            ]
        )
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        types = [type(e).__name__ for e in events]
        assert types == [
            'RunStarted',
            'TextMessageStart',
            'TextMessageContent',
            'TextMessageEnd',
            'RunFinished',
        ]

    def test_run_started_includes_thread_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        mock_session = _make_mock_session([_result_message()])
        monkeypatch.setattr(
            'marcel_core.agent.runner.session_manager',
            MagicMock(get_or_create=AsyncMock(return_value=mock_session)),
        )

        _, _, events = asyncio.run(_collect_stream(stream_response('shaun', 'cli', 'hi', 'conv-1')))
        started = events[0]
        assert isinstance(started, RunStarted)
        assert started.thread_id == 'conv-1'


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
            yield RunStarted(thread_id='test-conv')
            if tokens:
                yield TextMessageStart()
                for t in tokens:
                    yield TextMessageContent(text=t)
                yield TextMessageEnd()
            yield RunFinished(total_cost_usd=cost)

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
            msg1 = json.loads(ws.receive_text())  # text_message_start
            assert msg1['type'] == 'text_message_start'
            token1 = json.loads(ws.receive_text())
            token2 = json.loads(ws.receive_text())
            msg2 = json.loads(ws.receive_text())  # text_message_end
            assert msg2['type'] == 'text_message_end'
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
            ws.receive_text()  # text_message_start
            ws.receive_text()  # token
            ws.receive_text()  # text_message_end
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
        from marcel_core.memory.history import create_session

        conv_id = create_session('shaun', 'cli').session_id
        self._mock_stream(monkeypatch, ['reply'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': conv_id}))
            # No 'started' message since conversation already exists
            msg1 = json.loads(ws.receive_text())  # text_message_start
            assert msg1['type'] == 'text_message_start'
            token = json.loads(ws.receive_text())
            assert token['type'] == 'token'
            ws.receive_text()  # text_message_end
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'

    def test_tool_call_events_sent_over_websocket(self, tmp_path, monkeypatch):
        """ToolCallStart/End events are forwarded to the WebSocket client."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def fake_stream(*args, **kwargs):
            yield RunStarted(thread_id='test-conv')
            yield ToolCallStart(tool_call_id='t-1', tool_name='Read')
            yield ToolCallEnd(tool_call_id='t-1')
            yield RunFinished()

        monkeypatch.setattr('marcel_core.api.chat.stream_response', fake_stream)
        monkeypatch.setattr(
            'marcel_core.api.chat.extract_and_save_memories',
            lambda *a, **k: asyncio.sleep(0),
        )

        from marcel_core.memory.history import create_session

        conv_id = create_session('shaun', 'cli').session_id
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': conv_id}))
            msg1 = json.loads(ws.receive_text())
            assert msg1['type'] == 'tool_call_start'
            assert msg1['tool_name'] == 'Read'
            msg2 = json.loads(ws.receive_text())
            assert msg2['type'] == 'tool_call_end'
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'

    def test_wrong_api_token_rejected(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_api_token', 'my-secret')
        self._mock_stream(monkeypatch, ['ok'])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'token': 'wrong'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'

    def test_no_user_and_no_default_returns_error(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_default_user', '')
        self._mock_stream(monkeypatch, [])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello'}))  # no user field, no default
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'
            assert 'user' in msg['message'].lower()

    def test_invalid_user_slug_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        self._mock_stream(monkeypatch, [])
        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hello', 'user': 'INVALID SLUG!'}))
            msg = json.loads(ws.receive_text())
            assert msg['type'] == 'error'

    def test_stream_exception_sends_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def broken_stream(*args, **kwargs):
            raise RuntimeError('stream crashed')
            yield  # make it a generator

        monkeypatch.setattr('marcel_core.api.chat.stream_response', broken_stream)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            err = json.loads(ws.receive_text())
            assert err['type'] == 'error'

    def test_done_includes_turns_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def fake_stream(*args, **kwargs):
            yield RunStarted()
            yield TextMessageStart()
            yield TextMessageContent(text='hi')
            yield TextMessageEnd()
            yield RunFinished(total_cost_usd=0.01, num_turns=3)

        monkeypatch.setattr('marcel_core.api.chat.stream_response', fake_stream)
        monkeypatch.setattr('marcel_core.api.chat.extract_and_save_memories', lambda *a, **k: asyncio.sleep(0))

        with TestClient(app).websocket_connect('/ws/chat') as ws:
            ws.send_text(json.dumps({'text': 'hi', 'user': 'shaun', 'conversation': None}))
            ws.receive_text()  # started
            ws.receive_text()  # text_message_start
            ws.receive_text()  # token
            ws.receive_text()  # text_message_end
            done = json.loads(ws.receive_text())
            assert done['type'] == 'done'
            assert done.get('turns') == 3


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
