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


def test_message_with_tool_call_id_and_result_ref(temp_data_root: Path):
    """to_jsonl/from_jsonl roundtrip with tool_call_id, tool_name, result_ref, and is_error."""
    msg = HistoryMessage(
        role='tool',
        text='result',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='conv-1',
        tool_call_id='tc-x',
        tool_name='bash',
        result_ref='paste-abc',
        is_error=True,
    )
    line = msg.to_jsonl()
    assert '"tool_call_id"' in line
    assert '"tool_name"' in line
    assert '"result_ref"' in line
    assert '"is_error"' in line

    restored = HistoryMessage.from_jsonl(line)
    assert restored.tool_call_id == 'tc-x'
    assert restored.tool_name == 'bash'
    assert restored.result_ref == 'paste-abc'
    assert restored.is_error is True


def test_read_history_skips_blank_lines(temp_data_root: Path):
    """Blank lines in history file should be skipped."""
    user_dir = temp_data_root / 'users' / 'george'
    user_dir.mkdir(parents=True)
    history_file = user_dir / 'history.jsonl'
    good_line = HistoryMessage(
        role='user',
        text='hello',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
    ).to_jsonl()
    history_file.write_text(f'{good_line}\n\n   \n', encoding='utf-8')

    messages = read_history('george')
    assert len(messages) == 1


def test_read_recent_turns_no_limit_when_few_turns(temp_data_root: Path):
    """When num_turns >= available turns, all messages are returned."""
    from datetime import timedelta

    base = datetime(2026, 4, 9, tzinfo=timezone.utc)
    for i in range(2):
        append_message(
            'user',
            HistoryMessage(role='user', text=f'u{i}', timestamp=base + timedelta(seconds=i * 2), conversation_id='c1'),
        )
        append_message(
            'user',
            HistoryMessage(
                role='assistant', text=f'a{i}', timestamp=base + timedelta(seconds=i * 2 + 1), conversation_id='c1'
            ),
        )

    messages = read_recent_turns('user', 'c1', num_turns=10)
    assert len(messages) == 4  # all 4 messages returned


def test_read_history_skips_malformed_lines(temp_data_root: Path):
    """Lines that aren't valid JSON should be skipped with a warning."""
    user_dir = temp_data_root / 'users' / 'frank'
    user_dir.mkdir(parents=True)
    history_file = user_dir / 'history.jsonl'
    # Write one valid line and one malformed line
    good_line = HistoryMessage(
        role='user',
        text='hi',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
    ).to_jsonl()
    history_file.write_text(f'{good_line}\nnot-valid-json\n', encoding='utf-8')

    messages = read_history('frank')
    assert len(messages) == 1
    assert messages[0].text == 'hi'


def test_read_recent_turns_limits_correctly(temp_data_root: Path):
    """read_recent_turns should return only the last num_turns pairs."""
    from datetime import timedelta

    base = datetime(2026, 4, 9, tzinfo=timezone.utc)
    for i in range(5):
        append_message(
            'user',
            HistoryMessage(
                role='user', text=f'msg {i}', timestamp=base + timedelta(seconds=i * 2), conversation_id='c1'
            ),
        )
        append_message(
            'user',
            HistoryMessage(
                role='assistant', text=f'reply {i}', timestamp=base + timedelta(seconds=i * 2 + 1), conversation_id='c1'
            ),
        )

    messages = read_recent_turns('user', 'c1', num_turns=2)
    # Should contain only the last 2 user+assistant pairs = 4 messages
    user_msgs = [m for m in messages if m.role == 'user']
    assert len(user_msgs) == 2
    assert user_msgs[-1].text == 'msg 4'


def test_count_tokens_estimate_with_tool_calls():
    """count_tokens_estimate should include tool call sizes."""
    tool_call = ToolCall(id='tc-1', name='bash', arguments={'command': 'ls'})
    msg = HistoryMessage(
        role='assistant',
        text='Checking...',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
        tool_calls=[tool_call],
    )
    count = count_tokens_estimate([msg])
    assert count > 0  # At least some tokens from text + tool call
