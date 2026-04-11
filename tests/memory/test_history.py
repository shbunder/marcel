"""Tests for JSONL history module — message types and serialization."""

from datetime import datetime, timezone

from marcel_core.memory.history import (
    HistoryMessage,
    ToolCall,
)

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


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


def test_message_with_tool_call_id_and_result_ref():
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
