"""Tests for JSONL history module — per-session storage with legacy fallback."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from marcel_core.memory.history import (
    HistoryMessage,
    SessionMeta,
    ToolCall,
    append_message,
    create_session,
    list_sessions,
    read_history,
)


@pytest.fixture
def temp_data_root(tmp_path: Path):
    """Patch data_root to use temporary directory."""
    with patch('marcel_core.memory.history.data_root', return_value=tmp_path):
        yield tmp_path


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


# ---------------------------------------------------------------------------
# Per-session append and read
# ---------------------------------------------------------------------------


def test_append_and_read(temp_data_root: Path):
    """Test appending and reading messages to per-session files."""
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


def test_append_creates_session_file(temp_data_root: Path):
    """append_message should create per-session JSONL and meta files."""
    msg = HistoryMessage(
        role='user',
        text='hello',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='sess-1',
    )
    append_message('alice', msg, channel='telegram')

    # Session file should exist
    session_file = temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'sess-1.jsonl'
    assert session_file.exists()

    # Meta file should exist
    meta_file = temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'sess-1.meta.json'
    assert meta_file.exists()


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


def test_read_nonexistent_user(temp_data_root: Path):
    """Test reading history for user with no history."""
    messages = read_history('nonexistent_user')
    assert messages == []


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def test_create_session(temp_data_root: Path):
    """create_session creates meta and empty JSONL files."""
    meta = create_session('alice', 'telegram', session_id='sess-1', title='Test chat')

    assert meta.session_id == 'sess-1'
    assert meta.channel == 'telegram'
    assert meta.title == 'Test chat'

    # Files exist
    assert (temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'sess-1.jsonl').exists()
    assert (temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'sess-1.meta.json').exists()


def test_create_session_auto_id(temp_data_root: Path):
    """create_session without session_id generates a timestamp-based ID."""
    meta = create_session('alice', 'ios')
    assert meta.session_id  # non-empty
    assert 'T' in meta.session_id  # timestamp format


def test_list_sessions(temp_data_root: Path):
    """list_sessions returns sessions sorted by last_active."""
    base = datetime(2026, 4, 9, tzinfo=timezone.utc)

    create_session('alice', 'telegram', session_id='old-sess')
    append_message(
        'alice',
        HistoryMessage(
            role='user',
            text='old',
            timestamp=base,
            conversation_id='old-sess',
        ),
        channel='telegram',
    )

    create_session('alice', 'telegram', session_id='new-sess')
    append_message(
        'alice',
        HistoryMessage(
            role='user',
            text='new',
            timestamp=base + timedelta(hours=1),
            conversation_id='new-sess',
        ),
        channel='telegram',
    )

    sessions = list_sessions('alice')
    assert len(sessions) == 2
    assert sessions[0].session_id == 'new-sess'  # newest first


def test_list_sessions_filter_by_channel(temp_data_root: Path):
    """list_sessions can filter by channel."""
    create_session('alice', 'telegram', session_id='tg-1')
    create_session('alice', 'ios', session_id='ios-1')

    tg_sessions = list_sessions('alice', channel='telegram')
    assert len(tg_sessions) == 1
    assert tg_sessions[0].channel == 'telegram'


# ---------------------------------------------------------------------------
# Legacy fallback
# ---------------------------------------------------------------------------


def test_legacy_fallback_read(temp_data_root: Path):
    """read_history falls back to legacy history.jsonl when no per-session files exist."""
    user_dir = temp_data_root / 'users' / 'george'
    user_dir.mkdir(parents=True)
    legacy_file = user_dir / 'history.jsonl'
    msg = HistoryMessage(
        role='user',
        text='legacy msg',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
    )
    legacy_file.write_text(msg.to_jsonl() + '\n', encoding='utf-8')

    messages = read_history('george', conversation_id='c1')
    assert len(messages) == 1
    assert messages[0].text == 'legacy msg'


def test_legacy_skips_blank_lines(temp_data_root: Path):
    """Blank lines in legacy history file should be skipped."""
    user_dir = temp_data_root / 'users' / 'george'
    user_dir.mkdir(parents=True)
    legacy_file = user_dir / 'history.jsonl'
    good_line = HistoryMessage(
        role='user',
        text='hello',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
    ).to_jsonl()
    legacy_file.write_text(f'{good_line}\n\n   \n', encoding='utf-8')

    messages = read_history('george')
    assert len(messages) == 1


def test_legacy_skips_malformed_lines(temp_data_root: Path):
    """Malformed JSON lines in legacy file should be skipped."""
    user_dir = temp_data_root / 'users' / 'frank'
    user_dir.mkdir(parents=True)
    legacy_file = user_dir / 'history.jsonl'
    good_line = HistoryMessage(
        role='user',
        text='hi',
        timestamp=datetime(2026, 4, 9, tzinfo=timezone.utc),
        conversation_id='c1',
    ).to_jsonl()
    legacy_file.write_text(f'{good_line}\nnot-valid-json\n', encoding='utf-8')

    messages = read_history('frank')
    assert len(messages) == 1
    assert messages[0].text == 'hi'


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


def test_session_meta_roundtrip():
    """SessionMeta to_dict/from_dict roundtrip."""
    now = datetime.now(tz=timezone.utc)
    meta = SessionMeta(
        session_id='s1',
        channel='telegram',
        created_at=now,
        last_active=now,
        message_count=5,
        title='Chat about weather',
    )
    d = meta.to_dict()
    restored = SessionMeta.from_dict(d)
    assert restored.session_id == 's1'
    assert restored.channel == 'telegram'
    assert restored.message_count == 5
    assert restored.title == 'Chat about weather'


# ---------------------------------------------------------------------------
# Cross-channel isolation
# ---------------------------------------------------------------------------


def test_different_channels_separate_files(temp_data_root: Path):
    """Different channels store sessions in separate directories."""
    msg_tg = HistoryMessage(
        role='user',
        text='from telegram',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='tg-sess',
    )
    msg_ios = HistoryMessage(
        role='user',
        text='from ios',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='ios-sess',
    )

    append_message('alice', msg_tg, channel='telegram')
    append_message('alice', msg_ios, channel='ios')

    # Each channel has its own directory and session file
    tg_file = temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'tg-sess.jsonl'
    ios_file = temp_data_root / 'users' / 'alice' / 'history' / 'ios' / 'ios-sess.jsonl'
    assert tg_file.exists()
    assert ios_file.exists()

    # Sessions are isolated
    tg_msgs = read_history('alice', conversation_id='tg-sess')
    ios_msgs = read_history('alice', conversation_id='ios-sess')
    assert len(tg_msgs) == 1
    assert tg_msgs[0].text == 'from telegram'
    assert len(ios_msgs) == 1
    assert ios_msgs[0].text == 'from ios'
