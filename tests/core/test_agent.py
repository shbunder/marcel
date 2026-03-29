"""Tests for ISSUE-003: agent loop — context building, streaming, and memory extraction."""

import asyncio
import json

import claude_agent_sdk
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock
from fastapi.testclient import TestClient

from marcel_core.agent.context import build_system_prompt
from marcel_core.agent.memory_extract import _parse_and_save, extract_and_save_memories
from marcel_core.main import app
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _agen(*items):
    """Yield items as an async generator — used to mock claude_agent_sdk.query."""
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

    def test_includes_conversation_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import append_turn, new_conversation

        conv_id = new_conversation('shaun', 'cli')
        append_turn('shaun', conv_id, 'user', 'What time is it?')
        append_turn('shaun', conv_id, 'assistant', 'It is 3pm.')
        prompt = build_system_prompt('shaun', 'cli', conversation_id=conv_id)
        assert 'What time is it?' in prompt
        assert 'It is 3pm.' in prompt

    def test_no_conversation_section_when_no_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        prompt = build_system_prompt('shaun', 'cli', conversation_id=None)
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
# runner.py — stream_response
# ---------------------------------------------------------------------------


class TestStreamResponse:
    def test_yields_tokens_from_stream_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_stream_event('Hello'), _stream_event(' world')),
        )
        from marcel_core.agent.runner import stream_response

        tokens = asyncio.run(_collect(stream_response('shaun', 'cli', 'hi')))
        assert tokens == ['Hello', ' world']

    def test_falls_back_to_assistant_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_assistant_message('Fallback text')),
        )
        from marcel_core.agent.runner import stream_response

        tokens = asyncio.run(_collect(stream_response('shaun', 'cli', 'hi')))
        assert tokens == ['Fallback text']

    def test_ignores_assistant_message_when_stream_events_received(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event('streamed'),
                _assistant_message('full text'),
            ),
        )
        from marcel_core.agent.runner import stream_response

        tokens = asyncio.run(_collect(stream_response('shaun', 'cli', 'hi')))
        assert tokens == ['streamed']

    def test_skips_empty_text_deltas(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(
                _stream_event(''),
                _stream_event('real'),
                _stream_event(''),
            ),
        )
        from marcel_core.agent.runner import stream_response

        tokens = asyncio.run(_collect(stream_response('shaun', 'cli', 'hi')))
        assert tokens == ['real']


async def _collect(agen):
    return [x async for x in agen]


# ---------------------------------------------------------------------------
# memory_extract.py — _parse_and_save
# ---------------------------------------------------------------------------


class TestParseAndSave:
    def test_saves_single_topic(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        _parse_and_save('shaun', 'TOPIC: calendar\nCONTENT: Prefers mornings.')
        from marcel_core.storage import load_memory_file

        content = load_memory_file('shaun', 'calendar')
        assert 'Prefers mornings.' in content

    def test_saves_multiple_topics(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        response = 'TOPIC: family\nCONTENT: Has two kids.\nTOPIC: work\nCONTENT: Works remotely.'
        _parse_and_save('shaun', response)
        from marcel_core.storage import load_memory_file

        assert 'Has two kids.' in load_memory_file('shaun', 'family')
        assert 'Works remotely.' in load_memory_file('shaun', 'work')

    def test_appends_to_existing_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'calendar', '# Calendar\nOld fact.')
        _parse_and_save('shaun', 'TOPIC: calendar\nCONTENT: New fact.')
        from marcel_core.storage import load_memory_file

        content = load_memory_file('shaun', 'calendar')
        assert 'Old fact.' in content
        assert 'New fact.' in content

    def test_skips_malformed_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # No CONTENT: line — should not crash
        _parse_and_save('shaun', 'TOPIC: broken\njust some text without content marker')
        from marcel_core.storage import load_memory_file

        assert load_memory_file('shaun', 'broken') == ''


class TestExtractAndSaveMemories:
    def test_no_new_facts_skips_save(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_assistant_message('NO_NEW_FACTS')),
        )
        asyncio.run(extract_and_save_memories('shaun', 'hello', 'hi there', 'conv-1'))
        from marcel_core.storage import load_memory_index

        assert load_memory_index('shaun') == ''

    def test_saves_extracted_facts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(
            claude_agent_sdk,
            'query',
            lambda **_: _agen(_assistant_message('TOPIC: preferences\nCONTENT: Likes tea.')),
        )
        asyncio.run(extract_and_save_memories('shaun', 'I like tea', 'Noted!', 'conv-1'))
        from marcel_core.storage import load_memory_file

        assert 'Likes tea.' in load_memory_file('shaun', 'preferences')

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def boom(**_):
            raise RuntimeError('api down')
            yield  # make it an async generator

        monkeypatch.setattr(claude_agent_sdk, 'query', boom)
        # Should not raise
        asyncio.run(extract_and_save_memories('shaun', 'x', 'y', 'conv-1'))


# ---------------------------------------------------------------------------
# api/chat.py — WebSocket (mocked runner)
# ---------------------------------------------------------------------------


class TestChatWebSocket:
    def _mock_stream(self, monkeypatch, tokens: list[str]):
        async def fake_stream(*args, **kwargs):
            for t in tokens:
                yield t

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
            assert done == {'type': 'done'}

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
