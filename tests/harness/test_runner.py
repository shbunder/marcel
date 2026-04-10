"""Tests for harness/runner.py — stream_turn with a mocked pydantic-ai agent."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from marcel_core.harness.runner import (
    RunFinished,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
    _extract_tool_history,
    _tool_result_for_context,
    history_to_messages,
    stream_turn,
)
from marcel_core.memory.history import HistoryMessage, MessageRole, ToolCall
from marcel_core.storage import _root


@asynccontextmanager
async def _mock_run_stream(
    text_parts: list[str],
    *,
    all_messages: list | None = None,
    cost: int | None = None,
):
    """Return a mock agent.run_stream() context manager that yields text deltas."""

    async def _stream_text(*, delta: bool, debounce_by: float):
        for part in text_parts:
            yield part

    usage = MagicMock()
    usage.total_tokens = 100
    usage.request_tokens = 80
    usage.response_tokens = 20

    result = MagicMock()
    result.stream_text = _stream_text
    result.get_output = AsyncMock(return_value=None)
    result.usage = MagicMock(return_value=usage)
    result.all_messages = MagicMock(return_value=all_messages or [])

    yield result


def _make_mock_agent(text_parts: list[str], all_messages: list | None = None):
    """Return a mock pydantic-ai agent."""
    agent = MagicMock()
    agent.run_stream = lambda *args, **kwargs: _mock_run_stream(text_parts, all_messages=all_messages)
    return agent


# ---------------------------------------------------------------------------
# stream_turn tests
# ---------------------------------------------------------------------------


class TestStreamTurn:
    @pytest.mark.asyncio
    async def test_yields_run_started(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=_make_mock_agent(['Hello'])):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        types = [e.type for e in events]
        assert 'run_started' in types

    @pytest.mark.asyncio
    async def test_yields_text_delta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        with patch(
            'marcel_core.harness.runner.create_marcel_agent', return_value=_make_mock_agent(['Hello', ' world'])
        ):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        deltas = [e for e in events if isinstance(e, TextDelta)]
        assert len(deltas) == 2
        assert deltas[0].text == 'Hello'
        assert deltas[1].text == ' world'

    @pytest.mark.asyncio
    async def test_yields_run_finished(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=_make_mock_agent(['ok'])):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        finished = [e for e in events if isinstance(e, RunFinished)]
        assert len(finished) == 1
        assert finished[0].is_error is False

    @pytest.mark.asyncio
    async def test_appends_to_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        from marcel_core.memory.history import read_history

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=_make_mock_agent(['reply'])):
            async for _ in stream_turn('shaun', 'cli', 'what is 2+2?', 'conv-1'):
                pass

        messages = read_history('shaun', limit=10)
        texts = [m.text for m in messages]
        assert 'what is 2+2?' in texts
        assert 'reply' in texts

    @pytest.mark.asyncio
    async def test_error_yields_run_finished_with_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        @asynccontextmanager
        async def _boom_stream(*args, **kwargs):
            raise RuntimeError('agent failed')
            yield  # make it a generator  # noqa: unreachable

        boom_agent = MagicMock()
        boom_agent.run_stream = lambda *args, **kwargs: _boom_stream()

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=boom_agent):
            events = [e async for e in stream_turn('shaun', 'cli', 'error me', 'conv-1')]

        finished = [e for e in events if isinstance(e, RunFinished)]
        assert len(finished) == 1
        assert finished[0].is_error is True

    @pytest.mark.asyncio
    async def test_admin_user_gets_host_home_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('HOST_HOME', '/host/home')

        # Write admin role
        import json

        user_dir = tmp_path / 'users' / 'admin'
        user_dir.mkdir(parents=True)
        (user_dir / 'user.json').write_text(json.dumps({'role': 'admin'}))

        captured_deps = []

        @asynccontextmanager
        async def _capture_stream(user_text, *, deps, **kwargs):
            captured_deps.append(deps)

            async def _stream_text(*, delta, debounce_by):
                yield 'ok'

            result = MagicMock()
            result.stream_text = _stream_text
            result.get_output = AsyncMock()
            result.usage = MagicMock(return_value=MagicMock(total_tokens=10))
            result.all_messages = MagicMock(return_value=[])
            yield result

        agent = MagicMock()
        agent.run_stream = lambda user_text, **kwargs: _capture_stream(user_text, **kwargs)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            async for _ in stream_turn('admin', 'telegram', 'hi', 'conv-1'):
                pass

        assert len(captured_deps) == 1
        # Admin on non-CLI channel should get host home as cwd
        assert captured_deps[0].cwd == '/host/home'

    @pytest.mark.asyncio
    async def test_explicit_model_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        captured_model = []

        def _capture_create(model, **kwargs):
            captured_model.append(model)
            return _make_mock_agent(['hi'])

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_capture_create):
            async for _ in stream_turn('shaun', 'cli', 'hi', 'conv-1', model='gpt-4o'):
                pass

        assert captured_model[0] == 'gpt-4o'


# ---------------------------------------------------------------------------
# history_to_messages tests
# ---------------------------------------------------------------------------


class TestHistoryToMessages:
    def _msg(self, role: MessageRole, text: str | None, **kwargs) -> HistoryMessage:
        return HistoryMessage(
            role=role,
            text=text,
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='conv-1',
            **kwargs,
        )

    def test_converts_user_and_assistant(self):
        history = [self._msg('user', 'hello'), self._msg('assistant', 'hi there')]
        result = history_to_messages(history)
        assert len(result) == 2
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[1], ModelResponse)

    def test_converts_tool_messages(self):
        history = [
            self._msg('user', 'hello'),
            self._msg('assistant', None, tool_calls=[ToolCall(id='tc-1', name='bash', arguments={'command': 'ls'})]),
            self._msg('tool', 'file1\nfile2', tool_call_id='tc-1', tool_name='bash'),
            self._msg('assistant', 'Here are the files.'),
        ]
        result = history_to_messages(history)
        assert len(result) == 4
        # assistant with tool call
        assert isinstance(result[1], ModelResponse)
        assert len(result[1].parts) == 1
        assert isinstance(result[1].parts[0], ToolCallPart)
        assert result[1].parts[0].tool_name == 'bash'
        # tool result
        assert isinstance(result[2], ModelRequest)
        assert isinstance(result[2].parts[0], ToolReturnPart)
        assert result[2].parts[0].tool_name == 'bash'

    def test_converts_system_messages(self):
        history = [
            self._msg('system', 'context summary'),
            self._msg('user', 'hello'),
            self._msg('assistant', 'reply'),
        ]
        result = history_to_messages(history)
        assert len(result) == 3
        # System messages become UserPromptPart in a ModelRequest
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[0].parts[0], UserPromptPart)

    def test_skips_empty_text(self):
        history = [self._msg('user', None), self._msg('user', ''), self._msg('user', 'real')]
        result = history_to_messages(history)
        assert len(result) == 1

    def test_empty_history(self):
        assert history_to_messages([]) == []

    def test_preserves_content(self):
        history = [self._msg('user', 'what is 2+2?'), self._msg('assistant', '4')]
        result = history_to_messages(history)
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[1], ModelResponse)
        user_part = result[0].parts[0]
        assistant_part = result[1].parts[0]
        assert isinstance(user_part, UserPromptPart)
        assert isinstance(assistant_part, TextPart)
        assert user_part.content == 'what is 2+2?'
        assert assistant_part.content == '4'

    def test_assistant_with_text_and_tool_calls(self):
        history = [
            self._msg('user', 'check files'),
            self._msg(
                'assistant',
                'Let me check.',
                tool_calls=[ToolCall(id='tc-1', name='bash', arguments={'command': 'ls'})],
            ),
        ]
        result = history_to_messages(history)
        assert len(result) == 2
        response = result[1]
        assert isinstance(response, ModelResponse)
        assert len(response.parts) == 2
        assert isinstance(response.parts[0], TextPart)
        assert isinstance(response.parts[1], ToolCallPart)

    def test_consecutive_tool_returns_batched(self):
        """Multiple consecutive tool results should be batched into one ModelRequest."""
        history = [
            self._msg('user', 'do two things'),
            self._msg(
                'assistant',
                None,
                tool_calls=[
                    ToolCall(id='tc-1', name='bash', arguments={'command': 'ls'}),
                    ToolCall(id='tc-2', name='bash', arguments={'command': 'pwd'}),
                ],
            ),
            self._msg('tool', 'file1', tool_call_id='tc-1', tool_name='bash'),
            self._msg('tool', '/home', tool_call_id='tc-2', tool_name='bash'),
            self._msg('assistant', 'Done.'),
        ]
        result = history_to_messages(history)
        assert len(result) == 4  # user, assistant+tools, request(2 returns), assistant
        # The tool returns should be in a single ModelRequest
        tool_request = result[2]
        assert isinstance(tool_request, ModelRequest)
        assert len(tool_request.parts) == 2
        assert all(isinstance(p, ToolReturnPart) for p in tool_request.parts)


class TestToolResultForContext:
    """Tests for tiered tool result trimming."""

    def test_empty_result(self):
        assert _tool_result_for_context(None, 'bash', 0) == '(bash completed with no output)'
        assert _tool_result_for_context('', 'bash', 0) == '(bash completed with no output)'

    def test_recent_turn_full_result(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 2)
        assert result == content  # kept in full

    def test_medium_age_truncated(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 8)
        assert len(result) < len(content)
        assert 'truncated' in result

    def test_medium_age_small_kept(self):
        content = 'short result'
        result = _tool_result_for_context(content, 'bash', 8)
        assert result == content

    def test_old_turn_names_only(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 20)
        assert result.startswith('[bash result:')

    def test_always_keep_tools(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'memory_search', 20)
        assert result == content  # kept in full regardless of age

    def test_notify_always_kept(self):
        result = _tool_result_for_context('sent notification', 'notify', 20)
        assert result == 'sent notification'


class TestExtractToolHistory:
    """Tests for extracting tool call history from pydantic-ai messages."""

    def test_extracts_tool_calls_and_results(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content='list files')]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name='bash', args={'command': 'ls'}, tool_call_id='tc-1'),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name='bash', content='file1\nfile2', tool_call_id='tc-1'),
                ]
            ),
            ModelResponse(parts=[TextPart(content='Here are the files.')]),
        ]

        entries = _extract_tool_history(messages, 'shaun', 'conv-1')
        assert len(entries) == 2

        # Assistant with tool call
        assert entries[0].role == 'assistant'
        assert entries[0].tool_calls is not None
        assert entries[0].tool_calls[0].name == 'bash'
        assert entries[0].tool_calls[0].id == 'tc-1'

        # Tool result
        assert entries[1].role == 'tool'
        assert entries[1].tool_name == 'bash'
        assert entries[1].tool_call_id == 'tc-1'
        assert entries[1].text == 'file1\nfile2'

    def test_skips_text_only_responses(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content='hello')]),
            ModelResponse(parts=[TextPart(content='Hi there!')]),
        ]
        entries = _extract_tool_history(messages, 'shaun', 'conv-1')
        assert len(entries) == 0  # No tool calls

    def test_large_result_offloaded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        large_content = 'x' * 5000  # Above PASTE_THRESHOLD (1KB)
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name='bash', args={'command': 'cat bigfile'}, tool_call_id='tc-1'),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name='bash', content=large_content, tool_call_id='tc-1'),
                ]
            ),
        ]

        entries = _extract_tool_history(messages, 'shaun', 'conv-1')
        tool_entry = [e for e in entries if e.role == 'tool'][0]
        assert tool_entry.result_ref is not None
        assert tool_entry.result_ref.startswith('sha256:')
        # Text should be truncated preview
        assert tool_entry.text is not None
        assert len(tool_entry.text) <= 2000 + 50  # preview + suffix

    def test_error_result_marked(self):
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name='bash', args={'command': 'fail'}, tool_call_id='tc-1'),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name='bash',
                        content='command failed',
                        tool_call_id='tc-1',
                        outcome='failed',
                    ),
                ]
            ),
        ]
        entries = _extract_tool_history(messages, 'shaun', 'conv-1')
        tool_entry = [e for e in entries if e.role == 'tool'][0]
        assert tool_entry.is_error is True


class TestStreamTurnWithToolCalls:
    """Tests for stream_turn tool call extraction and event yielding."""

    @pytest.mark.asyncio
    async def test_tool_calls_stored_in_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        all_msgs = [
            ModelRequest(parts=[UserPromptPart(content='list files')]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name='bash', args={'command': 'ls'}, tool_call_id='tc-1'),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name='bash', content='file1\nfile2', tool_call_id='tc-1'),
                ]
            ),
            ModelResponse(parts=[TextPart(content='Here are the files.')]),
        ]

        agent = _make_mock_agent(['Here are the files.'], all_messages=all_msgs)
        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            async for _ in stream_turn('shaun', 'cli', 'list files', 'conv-1'):
                pass

        from marcel_core.memory.history import read_history

        messages = read_history('shaun')
        roles = [m.role for m in messages]
        assert 'tool' in roles
        tool_msgs = [m for m in messages if m.role == 'tool']
        assert tool_msgs[0].tool_name == 'bash'
        assert tool_msgs[0].tool_call_id == 'tc-1'

    @pytest.mark.asyncio
    async def test_tool_call_events_yielded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        all_msgs = [
            ModelRequest(parts=[UserPromptPart(content='hi')]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name='integration', args={'id': 'news.get'}, tool_call_id='tc-1'),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name='integration', content='news result', tool_call_id='tc-1'),
                ]
            ),
            ModelResponse(parts=[TextPart(content='Here is the news.')]),
        ]

        agent = _make_mock_agent(['Here is the news.'], all_messages=all_msgs)
        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            events = [e async for e in stream_turn('shaun', 'cli', 'news', 'conv-1')]

        started = [e for e in events if isinstance(e, ToolCallStarted)]
        completed = [e for e in events if isinstance(e, ToolCallCompleted)]
        assert len(started) == 1
        assert started[0].tool_name == 'integration'
        assert len(completed) == 1
        assert completed[0].tool_name == 'integration'


class TestStreamTurnWithHistory:
    @pytest.mark.asyncio
    async def test_passes_message_history_to_agent(self, tmp_path, monkeypatch):
        """Verify that prior conversation history is passed to run_stream."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        # Seed history with a prior turn
        from marcel_core.memory.history import append_message

        append_message(
            'shaun',
            HistoryMessage(
                role='user',
                text='previous question',
                timestamp=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
        )
        append_message(
            'shaun',
            HistoryMessage(
                role='assistant',
                text='previous answer',
                timestamp=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
        )

        captured_history = []

        @asynccontextmanager
        async def _capture_stream(user_text, *, deps, message_history=None, **kwargs):
            captured_history.append(message_history)

            async def _stream_text(*, delta, debounce_by):
                yield 'ok'

            result = MagicMock()
            result.stream_text = _stream_text
            result.get_output = AsyncMock()
            result.usage = MagicMock(return_value=MagicMock(total_tokens=10))
            result.all_messages = MagicMock(return_value=[])
            yield result

        agent = MagicMock()
        agent.run_stream = lambda user_text, **kwargs: _capture_stream(user_text, **kwargs)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            async for _ in stream_turn('shaun', 'cli', 'new question', 'conv-1'):
                pass

        assert len(captured_history) == 1
        history = captured_history[0]
        assert history is not None
        assert len(history) == 2  # prior user + prior assistant
        assert isinstance(history[0], ModelRequest)
        assert isinstance(history[1], ModelResponse)
        first_part = history[0].parts[0]
        assert isinstance(first_part, UserPromptPart)
        assert first_part.content == 'previous question'

    @pytest.mark.asyncio
    async def test_no_history_for_first_message(self, tmp_path, monkeypatch):
        """First message in a conversation should have empty history."""
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        captured_history = []

        @asynccontextmanager
        async def _capture_stream(user_text, *, deps, message_history=None, **kwargs):
            captured_history.append(message_history)

            async def _stream_text(*, delta, debounce_by):
                yield 'hello'

            result = MagicMock()
            result.stream_text = _stream_text
            result.get_output = AsyncMock()
            result.usage = MagicMock(return_value=MagicMock(total_tokens=10))
            result.all_messages = MagicMock(return_value=[])
            yield result

        agent = MagicMock()
        agent.run_stream = lambda user_text, **kwargs: _capture_stream(user_text, **kwargs)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            async for _ in stream_turn('shaun', 'cli', 'first message', 'conv-new'):
                pass

        assert captured_history[0] == []
