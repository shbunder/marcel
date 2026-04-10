"""Tests for harness/runner.py — stream_turn with a mocked pydantic-ai agent."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from marcel_core.harness.runner import (
    RunFinished,
    TextDelta,
    history_to_messages,
    stream_turn,
)
from marcel_core.memory.history import HistoryMessage, MessageRole
from marcel_core.storage import _root


@asynccontextmanager
async def _mock_run_stream(text_parts: list[str], *, cost: int | None = None):
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

    yield result


def _make_mock_agent(text_parts: list[str]):
    """Return a mock pydantic-ai agent."""
    agent = MagicMock()
    agent.run_stream = lambda *args, **kwargs: _mock_run_stream(text_parts)
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
    def _msg(self, role: MessageRole, text: str | None) -> HistoryMessage:
        return HistoryMessage(
            role=role,
            text=text,
            timestamp=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            conversation_id='conv-1',
        )

    def test_converts_user_and_assistant(self):
        history = [self._msg('user', 'hello'), self._msg('assistant', 'hi there')]
        result = history_to_messages(history)
        assert len(result) == 2
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[1], ModelResponse)

    def test_skips_tool_and_system(self):
        history = [
            self._msg('user', 'hello'),
            self._msg('tool', 'tool result'),
            self._msg('system', 'system note'),
            self._msg('assistant', 'reply'),
        ]
        result = history_to_messages(history)
        assert len(result) == 2

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
            yield result

        agent = MagicMock()
        agent.run_stream = lambda user_text, **kwargs: _capture_stream(user_text, **kwargs)

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=agent):
            async for _ in stream_turn('shaun', 'cli', 'first message', 'conv-new'):
                pass

        assert captured_history[0] == []
