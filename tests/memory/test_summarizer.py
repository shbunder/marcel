"""Scenario-based tests for memory/summarizer.py.

Covers: summarize_if_idle, summarize_active_segment, _generate_summary,
circuit breaker, chained summaries, and state management through realistic
conversation summarization workflows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.memory.conversation import SegmentSummary
from marcel_core.memory.history import HistoryMessage, MessageRole
from marcel_core.memory.summarizer import (
    _generate_summary,
    _get_state,
    _summarization_state,
    reset_summarization_state,
    summarize_active_segment,
    summarize_if_idle,
)
from marcel_core.storage import _root

_NOW = datetime.now(tz=timezone.utc)


def _msg(role: MessageRole, text: str = '', **kw) -> HistoryMessage:
    """Shorthand to create a HistoryMessage with defaults."""
    return HistoryMessage(role=role, text=text, timestamp=_NOW, conversation_id='test', **kw)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    _summarization_state.clear()


# ---------------------------------------------------------------------------
# summarize_if_idle
# ---------------------------------------------------------------------------


class TestSummarizeIfIdle:
    @pytest.mark.asyncio
    async def test_not_idle_skips(self):
        with patch('marcel_core.memory.summarizer.is_idle', return_value=False):
            result = await summarize_if_idle('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_idle_but_no_content(self):
        with (
            patch('marcel_core.memory.summarizer.is_idle', return_value=True),
            patch('marcel_core.memory.summarizer.has_active_content', return_value=False),
        ):
            result = await summarize_if_idle('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_idle_with_content_triggers_summarization(self):
        with (
            patch('marcel_core.memory.summarizer.is_idle', return_value=True),
            patch('marcel_core.memory.summarizer.has_active_content', return_value=True),
            patch(
                'marcel_core.memory.summarizer.summarize_active_segment',
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await summarize_if_idle('alice', 'telegram')
        assert result is True


# ---------------------------------------------------------------------------
# summarize_active_segment
# ---------------------------------------------------------------------------


class TestSummarizeActiveSegment:
    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self):
        state = _get_state('alice', 'telegram')
        state.consecutive_failures = 5

        result = await summarize_active_segment('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_no_active_content(self):
        with patch('marcel_core.memory.summarizer.has_active_content', return_value=False):
            result = await summarize_active_segment('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_no_channel_meta(self):
        with (
            patch('marcel_core.memory.summarizer.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.load_channel_meta', return_value=None),
        ):
            result = await summarize_active_segment('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_no_messages_in_segment(self):
        meta = MagicMock()
        meta.active_segment = 'seg-001'
        with (
            patch('marcel_core.memory.summarizer.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.load_channel_meta', return_value=meta),
            patch('marcel_core.memory.summarizer.read_segment', return_value=[]),
        ):
            result = await summarize_active_segment('alice', 'telegram')
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_summarization(self):
        meta = MagicMock()
        meta.active_segment = 'seg-001'
        messages = [
            _msg('user', 'Hello'),
            _msg('assistant', 'Hi there!'),
        ]
        new_meta = MagicMock()

        with (
            patch('marcel_core.memory.summarizer.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.load_channel_meta', return_value=meta),
            patch('marcel_core.memory.summarizer.read_segment', return_value=messages),
            patch('marcel_core.memory.summarizer.seal_active_segment', return_value=('seg-001', new_meta)),
            patch('marcel_core.memory.summarizer.strip_tool_results_from_segment', return_value=0),
            patch('marcel_core.memory.summarizer.load_latest_summary', return_value=None),
            patch(
                'marcel_core.memory.summarizer._generate_summary',
                new_callable=AsyncMock,
                return_value='The user said hello.',
            ),
            patch('marcel_core.memory.summarizer.save_summary'),
        ):
            result = await summarize_active_segment('alice', 'telegram', trigger='manual')

        assert result is True
        state = _get_state('alice', 'telegram')
        assert state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self):
        meta = MagicMock()
        meta.active_segment = 'seg-001'
        messages = [
            _msg('user', 'Hello'),
        ]

        with (
            patch('marcel_core.memory.summarizer.has_active_content', return_value=True),
            patch('marcel_core.memory.summarizer.load_channel_meta', return_value=meta),
            patch('marcel_core.memory.summarizer.read_segment', return_value=messages),
            patch('marcel_core.memory.summarizer.seal_active_segment', side_effect=RuntimeError('disk error')),
        ):
            result = await summarize_active_segment('alice', 'telegram')

        assert result is False
        state = _get_state('alice', 'telegram')
        assert state.consecutive_failures == 1


# ---------------------------------------------------------------------------
# _generate_summary
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_generates_without_previous(self):
        messages = [
            _msg('user', 'Book a flight'),
            _msg('assistant', 'Sure, where to?'),
        ]

        mock_result = MagicMock()
        mock_result.output = 'The user asked to book a flight.'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch('marcel_core.memory.summarizer.Agent', return_value=mock_agent):
            summary = await _generate_summary(messages)

        assert summary == 'The user asked to book a flight.'

    @pytest.mark.asyncio
    async def test_generates_with_previous_summary(self):
        messages = [
            _msg('user', 'Now book the hotel'),
        ]
        previous = SegmentSummary(
            segment_id='seg-000',
            created_at=datetime.now(tz=timezone.utc),
            trigger='idle',
            message_count=5,
            time_span_from=datetime.now(tz=timezone.utc),
            time_span_to=datetime.now(tz=timezone.utc),
            summary='The user planned a trip.',
        )

        mock_result = MagicMock()
        mock_result.output = 'The user planned a trip and is now booking a hotel.'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch('marcel_core.memory.summarizer.Agent', return_value=mock_agent):
            summary = await _generate_summary(messages, previous_summary=previous)

        assert 'hotel' in summary

    @pytest.mark.asyncio
    async def test_handles_all_message_types(self):
        from marcel_core.memory.history import ToolCall

        messages = [
            _msg('user', 'Check the weather'),
            _msg(
                'assistant',
                'Let me check.',
                tool_calls=[ToolCall(id='tc1', name='weather_check', arguments={})],
            ),
            _msg('tool', 'Sunny, 22°C', tool_name='weather_check'),
            _msg('system', 'Rate limit warning'),
        ]

        mock_result = MagicMock()
        mock_result.output = 'User checked weather.'
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch('marcel_core.memory.summarizer.Agent', return_value=mock_agent):
            summary = await _generate_summary(messages)

        assert summary == 'User checked weather.'


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestStateManagement:
    def test_get_state_creates_new(self):
        state = _get_state('bob', 'cli')
        assert state.consecutive_failures == 0

    def test_get_state_returns_same(self):
        s1 = _get_state('bob', 'cli')
        s1.consecutive_failures = 2
        s2 = _get_state('bob', 'cli')
        assert s2.consecutive_failures == 2

    def test_reset_state(self):
        state = _get_state('bob', 'cli')
        state.consecutive_failures = 5
        reset_summarization_state('bob', 'cli')
        new_state = _get_state('bob', 'cli')
        assert new_state.consecutive_failures == 0

    def test_reset_nonexistent_no_error(self):
        reset_summarization_state('nobody', 'nothing')
