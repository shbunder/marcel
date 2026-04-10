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
    count_tokens_estimate,
    create_compaction_summary,
    create_session,
    delete_session,
    get_session_meta,
    list_sessions,
    migrate_legacy_history,
    read_history,
    read_recent_turns,
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


def test_read_recent_turns(temp_data_root: Path):
    """Test reading recent turns (user + assistant pairs)."""
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

    messages = read_recent_turns('test_user', 'conv-1', num_turns=2)
    assert len(messages) == 4
    assert messages[0].text == 'User 1'


def test_read_nonexistent_user(temp_data_root: Path):
    """Test reading history for user with no history."""
    messages = read_history('nonexistent_user')
    assert messages == []


def test_read_recent_turns_no_limit_when_few_turns(temp_data_root: Path):
    """When num_turns >= available turns, all messages are returned."""
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
    assert len(messages) == 4


def test_read_recent_turns_limits_correctly(temp_data_root: Path):
    """read_recent_turns should return only the last num_turns pairs."""
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
    user_msgs = [m for m in messages if m.role == 'user']
    assert len(user_msgs) == 2
    assert user_msgs[-1].text == 'msg 4'


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_token_count_estimate():
    """Test token count estimation."""
    messages = [
        HistoryMessage(role='user', text='a' * 400, timestamp=datetime.now(tz=timezone.utc), conversation_id='c1'),
        HistoryMessage(role='assistant', text='b' * 800, timestamp=datetime.now(tz=timezone.utc), conversation_id='c1'),
    ]
    estimate = count_tokens_estimate(messages)
    assert 250 <= estimate <= 350


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
    assert count > 0


def test_create_compaction_summary():
    """Test creating compaction summary message."""
    summary = create_compaction_summary([], 'User discussed project plans.', 'conv-1')
    assert summary.role == 'system'
    assert summary.text is not None
    assert 'Context summary' in summary.text
    assert 'project plans' in summary.text


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

    # Create two sessions with different timestamps
    create_session('alice', 'telegram', session_id='old-sess')
    # Append to make it older
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


def test_delete_session(temp_data_root: Path):
    """delete_session removes JSONL and meta files."""
    create_session('alice', 'telegram', session_id='doomed')
    assert delete_session('alice', 'telegram', 'doomed') is True

    # Files gone
    assert not (temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'doomed.jsonl').exists()
    assert not (temp_data_root / 'users' / 'alice' / 'history' / 'telegram' / 'doomed.meta.json').exists()


def test_delete_session_not_found(temp_data_root: Path):
    """delete_session returns False for nonexistent session."""
    assert delete_session('alice', 'telegram', 'nope') is False


def test_get_session_meta(temp_data_root: Path):
    """get_session_meta returns metadata for existing session."""
    create_session('alice', 'cli', session_id='s1', title='My session')
    meta = get_session_meta('alice', 'cli', 's1')
    assert meta is not None
    assert meta.title == 'My session'


def test_get_session_meta_not_found(temp_data_root: Path):
    """get_session_meta returns None for nonexistent session."""
    assert get_session_meta('alice', 'cli', 'nope') is None


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
# Migration
# ---------------------------------------------------------------------------


def test_migrate_legacy_history(temp_data_root: Path):
    """migrate_legacy_history splits flat file into per-session files."""
    user_dir = temp_data_root / 'users' / 'bob'
    user_dir.mkdir(parents=True)
    legacy_file = user_dir / 'history.jsonl'

    # Write messages from two conversations
    base = datetime(2026, 4, 9, tzinfo=timezone.utc)
    lines = []
    for i in range(4):
        conv_id = 'conv-A' if i < 2 else 'conv-B'
        msg = HistoryMessage(
            role='user',
            text=f'msg {i}',
            timestamp=base + timedelta(seconds=i),
            conversation_id=conv_id,
        )
        lines.append(msg.to_jsonl())
    legacy_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    count = migrate_legacy_history('bob', default_channel='telegram')
    assert count == 2

    # Legacy file renamed
    assert not legacy_file.exists()
    assert (user_dir / 'history.jsonl.migrated').exists()

    # Per-session files created
    sess_a = temp_data_root / 'users' / 'bob' / 'history' / 'telegram' / 'conv-A.jsonl'
    sess_b = temp_data_root / 'users' / 'bob' / 'history' / 'telegram' / 'conv-B.jsonl'
    assert sess_a.exists()
    assert sess_b.exists()

    # Read via new API
    messages_a = read_history('bob', conversation_id='conv-A')
    assert len(messages_a) == 2

    messages_b = read_history('bob', conversation_id='conv-B')
    assert len(messages_b) == 2

    # Meta files created
    meta_a = get_session_meta('bob', 'telegram', 'conv-A')
    assert meta_a is not None
    assert meta_a.message_count == 2


def test_migrate_no_legacy_file(temp_data_root: Path):
    """migrate_legacy_history returns 0 when no legacy file exists."""
    count = migrate_legacy_history('nobody')
    assert count == 0


def test_migrate_idempotent(temp_data_root: Path):
    """Running migration twice doesn't duplicate data."""
    user_dir = temp_data_root / 'users' / 'carol'
    user_dir.mkdir(parents=True)
    legacy_file = user_dir / 'history.jsonl'

    msg = HistoryMessage(
        role='user',
        text='hello',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id='conv-1',
    )
    legacy_file.write_text(msg.to_jsonl() + '\n', encoding='utf-8')

    count1 = migrate_legacy_history('carol')
    assert count1 == 1

    # Second run: legacy file is renamed, should return 0
    count2 = migrate_legacy_history('carol')
    assert count2 == 0


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
