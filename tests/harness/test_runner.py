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
    _messages_to_model,
    _prime_read_skills_from_history,
    _tool_result_for_context,
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

        from marcel_core.memory.conversation import read_active_segment

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=_make_mock_agent(['reply'])):
            async for _ in stream_turn('shaun', 'cli', 'what is 2+2?', 'conv-1'):
                pass

        messages = read_active_segment('shaun', 'cli')
        texts = [m.text for m in messages]
        assert 'what is 2+2?' in texts
        assert 'reply' in texts

    @pytest.mark.asyncio
    async def test_error_yields_run_finished_with_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        @asynccontextmanager
        async def _boom_stream(*args, **kwargs):
            raise RuntimeError('agent failed')
            yield  # make it a generator  # pragma: no cover

        boom_agent = MagicMock()
        boom_agent.run_stream = lambda *args, **kwargs: _boom_stream()

        with patch('marcel_core.harness.runner.create_marcel_agent', return_value=boom_agent):
            events = [e async for e in stream_turn('shaun', 'cli', 'error me', 'conv-1')]

        finished = [e for e in events if isinstance(e, RunFinished)]
        assert len(finished) == 1
        assert finished[0].is_error is True

    @pytest.mark.asyncio
    async def test_admin_user_gets_home_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setenv('HOME', '/home/testuser')

        # Write admin role in profile.md frontmatter
        user_dir = tmp_path / 'users' / 'admin'
        user_dir.mkdir(parents=True)
        (user_dir / 'profile.md').write_text('---\nrole: admin\n---\n\n# Admin\n')

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
        # Admin on non-CLI channel should get $HOME as cwd
        assert captured_deps[0].cwd == '/home/testuser'

    @pytest.mark.asyncio
    async def test_explicit_model_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        captured_model = []

        def _capture_create(model, **kwargs):
            captured_model.append(model)
            return _make_mock_agent(['hi'])

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_capture_create):
            async for _ in stream_turn('shaun', 'cli', 'hi', 'conv-1', model='openai:gpt-4o'):
                pass

        assert captured_model[0] == 'openai:gpt-4o'


# ---------------------------------------------------------------------------
# ISSUE-076: tiered fallback chain tests
# ---------------------------------------------------------------------------


def _raising_agent(exc_factory):
    """Return a mock agent whose ``run_stream`` raises before yielding any text."""

    @asynccontextmanager
    async def _boom(*args, **kwargs):
        raise exc_factory()
        yield  # make this an async generator  # pragma: no cover

    agent = MagicMock()
    agent.run_stream = lambda *args, **kwargs: _boom()
    return agent


def _mid_stream_failing_agent(text_parts: list[str], exc_factory):
    """Return a mock agent that streams some text then raises mid-stream."""

    @asynccontextmanager
    async def _cm(*args, **kwargs):
        async def _stream_text(*, delta: bool, debounce_by: float):
            for part in text_parts:
                yield part
            raise exc_factory()

        result = MagicMock()
        result.stream_text = _stream_text
        result.get_output = AsyncMock(return_value=None)
        result.usage = MagicMock(return_value=MagicMock(total_tokens=0))
        result.all_messages = MagicMock(return_value=[])
        yield result

    agent = MagicMock()
    agent.run_stream = lambda *args, **kwargs: _cm()
    return agent


class TestStreamTurnFallbackChain:
    @pytest.mark.asyncio
    async def test_pre_stream_failure_silently_retries_tier_2(self, tmp_path, monkeypatch):
        """Tier 1 overloaded → tier 2 succeeds. User sees only tier 2's text."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(marcel_settings, 'marcel_fallback_model', None)

        calls: list[str] = []

        def _create(model, **kwargs):
            calls.append(model)
            if len(calls) == 1:
                # Tier 1 blows up before any text arrives (classic overloaded)
                return _raising_agent(lambda: RuntimeError('Overloaded'))
            return _make_mock_agent(['Hello from backup'])

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        deltas = [e for e in events if isinstance(e, TextDelta)]
        finished = [e for e in events if isinstance(e, RunFinished)]
        # Only tier 2's text should be visible, and no error
        assert len(deltas) == 1
        assert deltas[0].text == 'Hello from backup'
        assert finished[0].is_error is False
        # Both tiers were attempted
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_all_cloud_tiers_fail_emits_explain_text(self, tmp_path, monkeypatch):
        """Tier 1 and tier 2 both fail → tier 3 (local explain) streams an apology."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(marcel_settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(marcel_settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(marcel_settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        calls: list[str] = []

        def _create(model, **kwargs):
            calls.append(model)
            if len(calls) < 3:
                return _raising_agent(lambda: RuntimeError('Overloaded'))
            return _make_mock_agent(['Sorry, services are down.'])

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        deltas = [e for e in events if isinstance(e, TextDelta)]
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert any('Sorry' in e.text for e in deltas)
        # Explain tier committed successfully → is_error False
        assert finished[0].is_error is False
        assert calls == ['anthropic:claude-sonnet-4-6', 'openai:gpt-4o', 'local:qwen3.5:4b']

    @pytest.mark.asyncio
    async def test_all_tiers_unreachable_surfaces_error(self, tmp_path, monkeypatch):
        """Every tier raises pre-stream → hardcoded error text, is_error=True."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')
        monkeypatch.setattr(marcel_settings, 'marcel_fallback_model', 'local:qwen3.5:4b')
        monkeypatch.setattr(marcel_settings, 'marcel_local_llm_url', 'http://127.0.0.1:11434/v1')
        monkeypatch.setattr(marcel_settings, 'marcel_local_llm_model', 'qwen3.5:4b')

        def _create(model, **kwargs):
            return _raising_agent(lambda: RuntimeError('Overloaded'))

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        deltas = [e for e in events if isinstance(e, TextDelta)]
        finished = [e for e in events if isinstance(e, RunFinished)]
        assert finished[0].is_error is True
        # The visible text should include 'Error' but not a raw traceback dump
        assert any('Error' in e.text for e in deltas)

    @pytest.mark.asyncio
    async def test_mid_stream_failure_does_not_retry(self, tmp_path, monkeypatch):
        """Mid-stream failure keeps the partial text + an error tail; no tier-2 retry."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')

        calls: list[str] = []

        def _create(model, **kwargs):
            calls.append(model)
            if len(calls) == 1:
                return _mid_stream_failing_agent(['Hello '], lambda: RuntimeError('Overloaded'))
            # If tier 2 is ever called, this assertion will trip the test
            raise AssertionError('tier 2 should not be called after mid-stream failure')

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        deltas = [e for e in events if isinstance(e, TextDelta)]
        finished = [e for e in events if isinstance(e, RunFinished)]
        # The partial text is preserved
        assert any('Hello' in e.text for e in deltas)
        # And an error tail was appended
        assert any('Error mid-response' in e.text for e in deltas)
        assert finished[0].is_error is True
        assert calls == ['anthropic:claude-sonnet-4-6']

    @pytest.mark.asyncio
    async def test_permanent_error_skips_chain(self, tmp_path, monkeypatch):
        """Permanent errors (e.g. validation) short-circuit immediately on tier 1."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')

        calls: list[str] = []

        def _create(model, **kwargs):
            calls.append(model)
            return _raising_agent(lambda: ValueError("Skill 'foo' not found"))

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            events = [e async for e in stream_turn('shaun', 'cli', 'hi', 'conv-1')]

        finished = [e for e in events if isinstance(e, RunFinished)]
        assert finished[0].is_error is True
        # Only tier 1 was tried
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_channel_pinned_model_overrides_tier_1_only(self, tmp_path, monkeypatch):
        """A channel model pin replaces tier 1 only; tier 2/3 still come from env vars."""
        from marcel_core.config import settings as marcel_settings

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(marcel_settings, 'marcel_standard_backup_model', 'openai:gpt-4o')

        # Pin the channel to a specific model
        from marcel_core.storage.settings import save_channel_model

        save_channel_model('shaun', 'cli', 'anthropic:claude-opus-4-6')

        calls: list[str] = []

        def _create(model, **kwargs):
            calls.append(model)
            if len(calls) == 1:
                return _raising_agent(lambda: RuntimeError('Overloaded'))
            return _make_mock_agent(['ok'])

        with patch('marcel_core.harness.runner.create_marcel_agent', side_effect=_create):
            async for _ in stream_turn('shaun', 'cli', 'hi', 'conv-1'):
                pass

        # Tier 1 = channel pin, tier 2 = MARCEL_BACKUP_MODEL
        assert calls == ['anthropic:claude-opus-4-6', 'openai:gpt-4o']


# ---------------------------------------------------------------------------
# _messages_to_model tests
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
        result = _messages_to_model(history)
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
        result = _messages_to_model(history)
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
        result = _messages_to_model(history)
        assert len(result) == 3
        # System messages become UserPromptPart in a ModelRequest
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[0].parts[0], UserPromptPart)

    def test_skips_empty_text(self):
        history = [self._msg('user', None), self._msg('user', ''), self._msg('user', 'real')]
        result = _messages_to_model(history)
        assert len(result) == 1

    def test_empty_history(self):
        assert _messages_to_model([]) == []

    def test_preserves_content(self):
        history = [self._msg('user', 'what is 2+2?'), self._msg('assistant', '4')]
        result = _messages_to_model(history)
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
        result = _messages_to_model(history)
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
        result = _messages_to_model(history)
        assert len(result) == 4  # user, assistant+tools, request(2 returns), assistant
        # The tool returns should be in a single ModelRequest
        tool_request = result[2]
        assert isinstance(tool_request, ModelRequest)
        assert len(tool_request.parts) == 2
        assert all(isinstance(p, ToolReturnPart) for p in tool_request.parts)


class TestToolResultForContext:
    """Tests for aggressive tool result lifecycle.

    Lifecycle: current turn (0) = full, previous turn (1) = preview, older (2+) = name-only.
    """

    def test_empty_result(self):
        assert _tool_result_for_context(None, 'bash', 0) == '(bash completed with no output)'
        assert _tool_result_for_context('', 'bash', 0) == '(bash completed with no output)'

    def test_current_turn_full_result(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 0)
        assert result == content  # kept in full

    def test_previous_turn_truncated(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 1)
        assert len(result) < len(content)
        assert 'truncated' in result

    def test_previous_turn_small_kept(self):
        content = 'short result'
        result = _tool_result_for_context(content, 'bash', 1)
        assert result == content

    def test_old_turn_name_only(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'bash', 2)
        assert result == '[Used bash]'

    def test_always_keep_tools(self):
        content = 'x' * 5000
        result = _tool_result_for_context(content, 'marcel', 20)
        assert result == content  # kept in full regardless of age

    def test_marcel_tool_always_kept_long(self):
        """Marcel tool results (search_memory, read_skill, etc.) are kept in full."""
        result = _tool_result_for_context('search results here', 'marcel', 20)
        assert result == 'search results here'


class TestPrimeReadSkillsFromHistory:
    """Tests for _prime_read_skills_from_history — priming per-turn read_skills."""

    def test_adds_skill_from_past_read_skill_call(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content='check calendar')]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='marcel',
                        args={'action': 'read_skill', 'name': 'icloud'},
                        tool_call_id='tc-1',
                    ),
                ]
            ),
            ModelRequest(parts=[ToolReturnPart(tool_name='marcel', content='...', tool_call_id='tc-1')]),
        ]
        read_skills: set[str] = set()
        _prime_read_skills_from_history(messages, read_skills)
        assert read_skills == {'icloud'}

    def test_ignores_non_read_skill_marcel_calls(self):
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='marcel',
                        args={'action': 'search_memory', 'query': 'anything'},
                        tool_call_id='tc-1',
                    ),
                ]
            ),
        ]
        read_skills: set[str] = set()
        _prime_read_skills_from_history(messages, read_skills)
        assert read_skills == set()

    def test_ignores_other_tools(self):
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='integration',
                        args={'id': 'icloud.calendar'},
                        tool_call_id='tc-1',
                    ),
                ]
            ),
        ]
        read_skills: set[str] = set()
        _prime_read_skills_from_history(messages, read_skills)
        assert read_skills == set()

    def test_collects_multiple_skills(self):
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='marcel',
                        args={'action': 'read_skill', 'name': 'icloud'},
                        tool_call_id='tc-1',
                    ),
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='marcel',
                        args={'action': 'read_skill', 'name': 'banking'},
                        tool_call_id='tc-2',
                    ),
                ]
            ),
        ]
        read_skills: set[str] = set()
        _prime_read_skills_from_history(messages, read_skills)
        assert read_skills == {'icloud', 'banking'}

    def test_preserves_existing_entries(self):
        read_skills: set[str] = {'news'}
        _prime_read_skills_from_history([], read_skills)
        assert read_skills == {'news'}


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

        from marcel_core.memory.conversation import read_active_segment

        messages = read_active_segment('shaun', 'cli')
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

        # Seed history with a prior turn (via segment-based storage)
        from marcel_core.memory.conversation import append_to_segment

        append_to_segment(
            'shaun',
            'cli',
            HistoryMessage(
                role='user',
                text='previous question',
                timestamp=datetime(2026, 4, 10, 11, 0, tzinfo=timezone.utc),
                conversation_id='conv-1',
            ),
        )
        append_to_segment(
            'shaun',
            'cli',
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
