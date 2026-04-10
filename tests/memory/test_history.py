"""Tests for JSONL history module."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from marcel_core.memory.history import (
    HistoryMessage,
    ToolCall,
    append_message,
    count_tokens_estimate,
    create_compaction_summary,
    read_history,
    read_recent_turns,
)


@pytest.fixture
def temp_data_root(tmp_path: Path):
    """Patch data_root to use temporary directory."""
    with patch('marcel_core.memory.history.data_root', return_value=tmp_path):
        yield tmp_path


def test_message_serialization():
    """Test HistoryMessage to/from JSONL."""
    msg = HistoryMessage(
        role='user',
        text='Hello Marcel',
        timestamp=datetime(2026, 4, 9, 10, 30, 0, tzinfo=timezone.utc),
        conversation_id='conv-123',
    )

    jsonl = msg.to_jsonl()
    assert '"role"' in jsonl and '"user"' in jsonl
    assert '"text"' in jsonl and '"Hello Marcel"' in jsonl
    assert '"conversation_id"' in jsonl and '"conv-123"' in jsonl

    parsed = HistoryMessage.from_jsonl(jsonl)
    assert parsed.role == 'user'
    assert parsed.text == 'Hello Marcel'
    assert parsed.conversation_id == 'conv-123'
    assert parsed.timestamp == msg.timestamp


def test_message_with_tool_calls():
    """Test message with tool calls serialization."""
    tool_call = ToolCall(id='tc-1', name='bash', arguments={'command': 'ls -la'})
    msg = HistoryMessage(
        role='assistant',
        text='Let me check.',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='conv-123',
        tool_calls=[tool_call],
    )

    jsonl = msg.to_jsonl()
    parsed = HistoryMessage.from_jsonl(jsonl)

    assert parsed.tool_calls is not None
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == 'bash'
    assert parsed.tool_calls[0].arguments == {'command': 'ls -la'}


def test_append_and_read(temp_data_root: Path):
    """Test appending and reading messages."""
    msg1 = HistoryMessage(
        role='user',
        text='First message',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='conv-1',
    )
    msg2 = HistoryMessage(
        role='assistant',
        text='Second message',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='conv-1',
    )

    append_message('test_user', msg1)
    append_message('test_user', msg2)

    messages = read_history('test_user')
    assert len(messages) == 2
    assert messages[0].text == 'First message'
    assert messages[1].text == 'Second message'


def test_read_filtered_by_conversation(temp_data_root: Path):
    """Test reading history filtered by conversation_id."""
    for i in range(3):
        msg = HistoryMessage(
            role='user',
            text=f'Message {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1' if i < 2 else 'conv-2',
        )
        append_message('test_user', msg)

    conv1_messages = read_history('test_user', conversation_id='conv-1')
    assert len(conv1_messages) == 2

    conv2_messages = read_history('test_user', conversation_id='conv-2')
    assert len(conv2_messages) == 1


def test_read_with_limit(temp_data_root: Path):
    """Test reading with limit returns most recent messages."""
    for i in range(10):
        msg = HistoryMessage(
            role='user',
            text=f'Message {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        append_message('test_user', msg)

    messages = read_history('test_user', limit=3)
    assert len(messages) == 3
    assert messages[-1].text == 'Message 9'  # Most recent


def test_read_recent_turns(temp_data_root: Path):
    """Test reading recent turns (user + assistant pairs)."""
    # Create 3 turns
    for i in range(3):
        user_msg = HistoryMessage(
            role='user',
            text=f'User {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        assistant_msg = HistoryMessage(
            role='assistant',
            text=f'Assistant {i}',
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        )
        append_message('test_user', user_msg)
        append_message('test_user', assistant_msg)

    # Read last 2 turns
    messages = read_recent_turns('test_user', 'conv-1', num_turns=2)
    assert len(messages) == 4  # 2 turns × 2 messages
    assert messages[0].text == 'User 1'


def test_token_count_estimate():
    """Test token count estimation."""
    messages = [
        HistoryMessage(
            role='user',
            text='a' * 400,  # ~100 tokens
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        ),
        HistoryMessage(
            role='assistant',
            text='b' * 800,  # ~200 tokens
            timestamp=datetime.now(tz=timezone.utc),
            conversation_id='conv-1',
        ),
    ]

    estimate = count_tokens_estimate(messages)
    assert 250 <= estimate <= 350  # ~300 tokens (rough estimate)


def test_create_compaction_summary():
    """Test creating compaction summary message."""
    messages = []  # Not used in current implementation
    summary = create_compaction_summary(messages, 'User discussed project plans.', 'conv-1')

    assert summary.role == 'system'
    assert summary.text is not None
    assert 'Context summary' in summary.text
    assert 'project plans' in summary.text
    assert summary.conversation_id == 'conv-1'


def test_read_nonexistent_user(temp_data_root: Path):
    """Test reading history for user with no history file."""
    messages = read_history('nonexistent_user')
    assert messages == []
