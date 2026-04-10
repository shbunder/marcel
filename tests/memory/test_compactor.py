"""Tests for auto-compaction module."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.memory.compactor import (
    COMPACTION_THRESHOLD,
    MAX_COMPACTION_FAILURES,
    PRESERVE_RECENT_TURNS,
    check_and_compact,
    reset_compaction_state,
)
from marcel_core.memory.history import HistoryMessage


@pytest.fixture
def mock_messages():
    """Create mock conversation messages."""
    messages = []
    for i in range(10):
        user_msg = HistoryMessage(
            role='user',
            text=f'User message {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        assistant_msg = HistoryMessage(
            role='assistant',
            text=f'Assistant response {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        messages.extend([user_msg, assistant_msg])
    return messages


@pytest.fixture(autouse=True)
def reset_state():
    """Reset compaction state before each test."""
    reset_compaction_state('conv-1')
    yield
    reset_compaction_state('conv-1')


@pytest.mark.asyncio
async def test_no_compaction_below_threshold(mock_messages):
    """Test that compaction doesn't run when below token threshold."""
    # Mock low token count
    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=mock_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=1000),
    ):
        result = await check_and_compact('test_user', 'conv-1')
        assert result is False  # No compaction performed


@pytest.mark.asyncio
async def test_compaction_above_threshold(mock_messages):
    """Test that compaction runs when above token threshold."""
    mock_agent_result = MagicMock()
    mock_agent_result.output = 'This is a summary of the conversation.'

    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=mock_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=COMPACTION_THRESHOLD + 1000),
        patch('marcel_core.memory.compactor.Agent') as mock_agent_class,
        patch('marcel_core.memory.compactor.append_message') as mock_append,
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.return_value = mock_agent_result
        mock_agent_class.return_value = mock_agent_instance

        result = await check_and_compact('test_user', 'conv-1')
        assert result is True  # Compaction performed
        mock_append.assert_called_once()  # Summary appended


@pytest.mark.asyncio
async def test_force_compaction(mock_messages):
    """Test that force=True bypasses threshold check."""
    mock_agent_result = MagicMock()
    mock_agent_result.output = 'Forced summary.'

    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=mock_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=100),
        patch('marcel_core.memory.compactor.Agent') as mock_agent_class,
        patch('marcel_core.memory.compactor.append_message'),
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.return_value = mock_agent_result
        mock_agent_class.return_value = mock_agent_instance

        result = await check_and_compact('test_user', 'conv-1', force=True)
        assert result is True


@pytest.mark.asyncio
async def test_circuit_breaker_after_failures(mock_messages):
    """Test that circuit breaker stops compaction after max failures."""
    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=mock_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=COMPACTION_THRESHOLD + 1000),
        patch('marcel_core.memory.compactor.Agent') as mock_agent_class,
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.side_effect = Exception('AI failed')
        mock_agent_class.return_value = mock_agent_instance

        # Trigger failures up to MAX_COMPACTION_FAILURES
        for _ in range(MAX_COMPACTION_FAILURES):
            result = await check_and_compact('test_user', 'conv-1')
            assert result is False

        # Circuit breaker should now be active - no more attempts
        with patch('marcel_core.memory.compactor.Agent') as mock_agent_class_cb:
            result = await check_and_compact('test_user', 'conv-1')
            assert result is False
            mock_agent_class_cb.assert_not_called()  # Should not even try


@pytest.mark.asyncio
async def test_preserves_recent_turns(mock_messages):
    """Test that recent turns are preserved during compaction."""
    mock_agent_result = MagicMock()
    mock_agent_result.output = 'Summary of old messages.'

    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=mock_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=COMPACTION_THRESHOLD + 1000),
        patch('marcel_core.memory.compactor.Agent') as mock_agent_class,
        patch('marcel_core.memory.compactor.append_message'),
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.return_value = mock_agent_result
        mock_agent_class.return_value = mock_agent_instance

        await check_and_compact('test_user', 'conv-1')

        # Check that summarizer was called with correct split
        call_args = mock_agent_instance.run.call_args
        prompt = call_args[0][0]

        # Should summarize old messages (not the last PRESERVE_RECENT_TURNS)
        # With 10 turns, should summarize first 5, preserve last 5
        assert 'User message 0' in prompt  # Old message included
        assert f'User message {9 - PRESERVE_RECENT_TURNS}' in prompt


@pytest.mark.asyncio
async def test_insufficient_history_for_compaction(mock_messages):
    """Test that compaction skips when there aren't enough turns."""
    # Only 2 turns (less than PRESERVE_RECENT_TURNS)
    short_messages = mock_messages[:4]

    with (
        patch('marcel_core.memory.compactor.read_recent_turns', return_value=short_messages),
        patch('marcel_core.memory.compactor.count_tokens_estimate', return_value=COMPACTION_THRESHOLD + 1000),
    ):
        result = await check_and_compact('test_user', 'conv-1')
        assert result is False  # Not enough history
