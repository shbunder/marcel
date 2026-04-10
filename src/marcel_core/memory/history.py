"""JSONL conversation history — structured, parseable turn-by-turn log.

History is stored per-session in individual JSONL files::

    data/users/{slug}/history/{channel}/{session_id}.jsonl
    data/users/{slug}/history/{channel}/{session_id}.meta.json

Each JSONL line is a JSON object with:
- role: 'user' | 'assistant' | 'tool' | 'system'
- text: message content (or None if result_ref used)
- timestamp: ISO 8601 UTC
- conversation_id: session identifier (kept for backwards compatibility)
- tool_calls: list of {id, name, arguments} for assistant messages
- tool_call_id: reference for tool messages
- tool_name: tool name for tool-role messages
- result_ref: content hash for large tool results (stored in paste store)
- is_error: boolean for tool errors

Legacy flat ``history.jsonl`` files are read transparently as a fallback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)

MessageRole = Literal['user', 'assistant', 'tool', 'system']


@dataclass
class ToolCall:
    """A tool invocation from an assistant message."""

    id: str
    name: str
    arguments: dict


@dataclass
class HistoryMessage:
    """A single message in the conversation history."""

    role: MessageRole
    text: str | None
    timestamp: datetime
    conversation_id: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None  # tool name for role='tool' messages
    result_ref: str | None = None
    is_error: bool = False

    def to_jsonl(self) -> str:
        """Serialize to a single JSON line."""
        obj = {
            'role': self.role,
            'text': self.text,
            'timestamp': self.timestamp.isoformat(),
            'conversation_id': self.conversation_id,
        }
        if self.tool_calls:
            obj['tool_calls'] = [{'id': tc.id, 'name': tc.name, 'arguments': tc.arguments} for tc in self.tool_calls]
        if self.tool_call_id:
            obj['tool_call_id'] = self.tool_call_id
        if self.tool_name:
            obj['tool_name'] = self.tool_name
        if self.result_ref:
            obj['result_ref'] = self.result_ref
        if self.is_error:
            obj['is_error'] = True
        return json.dumps(obj, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> HistoryMessage:
        """Deserialize from a JSON line."""
        obj = json.loads(line)
        tool_calls = None
        if 'tool_calls' in obj:
            tool_calls = [ToolCall(id=tc['id'], name=tc['name'], arguments=tc['arguments']) for tc in obj['tool_calls']]
        return cls(
            role=obj['role'],
            text=obj.get('text'),
            timestamp=datetime.fromisoformat(obj['timestamp']),
            conversation_id=obj['conversation_id'],
            tool_calls=tool_calls,
            tool_call_id=obj.get('tool_call_id'),
            tool_name=obj.get('tool_name'),
            result_ref=obj.get('result_ref'),
            is_error=obj.get('is_error', False),
        )


# ---------------------------------------------------------------------------
# Session metadata
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    """Metadata for a conversation session."""

    session_id: str
    channel: str
    created_at: datetime
    last_active: datetime
    message_count: int = 0
    title: str | None = None

    def to_dict(self) -> dict:
        return {
            'session_id': self.session_id,
            'channel': self.channel,
            'created_at': self.created_at.isoformat(),
            'last_active': self.last_active.isoformat(),
            'message_count': self.message_count,
            'title': self.title,
        }

    @classmethod
    def from_dict(cls, obj: dict) -> SessionMeta:
        return cls(
            session_id=obj['session_id'],
            channel=obj['channel'],
            created_at=datetime.fromisoformat(obj['created_at']),
            last_active=datetime.fromisoformat(obj['last_active']),
            message_count=obj.get('message_count', 0),
            title=obj.get('title'),
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _sessions_dir(user_slug: str) -> Path:
    """Return the root history directory for a user."""
    return data_root() / 'users' / user_slug / 'history'


def _session_dir(user_slug: str, channel: str) -> Path:
    """Return the channel directory for a user's sessions."""
    return _sessions_dir(user_slug) / channel


def _session_path(user_slug: str, channel: str, session_id: str) -> Path:
    """Return the JSONL path for a specific session."""
    return _session_dir(user_slug, channel) / f'{session_id}.jsonl'


def _session_meta_path(user_slug: str, channel: str, session_id: str) -> Path:
    """Return the metadata JSON path for a specific session."""
    return _session_dir(user_slug, channel) / f'{session_id}.meta.json'


def _legacy_history_path(user_slug: str) -> Path:
    """Return the legacy flat history.jsonl path (for migration fallback)."""
    return data_root() / 'users' / user_slug / 'history.jsonl'


def _resolve_channel(user_slug: str, conversation_id: str) -> str | None:
    """Find which channel directory contains a given session_id.

    Scans channel directories under the user's history root.
    Returns None if not found.
    """
    sessions_root = _sessions_dir(user_slug)
    if not sessions_root.exists():
        return None
    for channel_dir in sessions_root.iterdir():
        if not channel_dir.is_dir():
            continue
        if (channel_dir / f'{conversation_id}.jsonl').exists():
            return channel_dir.name
    return None


# ---------------------------------------------------------------------------
# Session metadata operations
# ---------------------------------------------------------------------------


def _load_meta(user_slug: str, channel: str, session_id: str) -> SessionMeta | None:
    """Load session metadata, or None if it doesn't exist."""
    path = _session_meta_path(user_slug, channel, session_id)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return SessionMeta.from_dict(obj)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _save_meta(user_slug: str, channel: str, meta: SessionMeta) -> None:
    """Write session metadata to disk."""
    path = _session_meta_path(user_slug, channel, meta.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta.to_dict(), indent=2), encoding='utf-8')


def _touch_meta(user_slug: str, channel: str, session_id: str) -> None:
    """Update last_active and increment message_count on an existing session.

    Creates a minimal meta file if none exists.
    """
    meta = _load_meta(user_slug, channel, session_id)
    now = datetime.now(tz=timezone.utc)
    if meta is None:
        meta = SessionMeta(
            session_id=session_id,
            channel=channel,
            created_at=now,
            last_active=now,
            message_count=1,
        )
    else:
        meta.last_active = now
        meta.message_count += 1
    _save_meta(user_slug, channel, meta)


# ---------------------------------------------------------------------------
# Public session management API
# ---------------------------------------------------------------------------


def list_sessions(
    user_slug: str,
    channel: str | None = None,
    limit: int = 50,
) -> list[SessionMeta]:
    """List sessions for a user, optionally filtered by channel.

    Returns sessions sorted by last_active (newest first), up to *limit*.
    """
    sessions_root = _sessions_dir(user_slug)
    if not sessions_root.exists():
        return []

    channels = [sessions_root / channel] if channel else [d for d in sessions_root.iterdir() if d.is_dir()]
    results: list[SessionMeta] = []

    for channel_dir in channels:
        if not channel_dir.is_dir():
            continue
        for meta_file in channel_dir.glob('*.meta.json'):
            try:
                obj = json.loads(meta_file.read_text(encoding='utf-8'))
                results.append(SessionMeta.from_dict(obj))
            except (json.JSONDecodeError, KeyError, OSError):
                continue

    results.sort(key=lambda m: m.last_active, reverse=True)
    return results[:limit]


def create_session(
    user_slug: str,
    channel: str,
    session_id: str | None = None,
    title: str | None = None,
) -> SessionMeta:
    """Create a new session and return its metadata.

    If *session_id* is not provided, one is generated from the current
    UTC timestamp (``YYYY-MM-DDTHH-MM``).
    """
    now = datetime.now(tz=timezone.utc)
    if session_id is None:
        session_id = now.strftime('%Y-%m-%dT%H-%M')

    meta = SessionMeta(
        session_id=session_id,
        channel=channel,
        created_at=now,
        last_active=now,
        title=title,
    )
    _save_meta(user_slug, channel, meta)

    # Create the (empty) JSONL file so _resolve_channel can find it
    path = _session_path(user_slug, channel, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    return meta


def delete_session(user_slug: str, channel: str, session_id: str) -> bool:
    """Delete a session's JSONL and metadata files. Returns True if deleted."""
    jsonl = _session_path(user_slug, channel, session_id)
    meta = _session_meta_path(user_slug, channel, session_id)
    deleted = False
    if jsonl.exists():
        jsonl.unlink()
        deleted = True
    if meta.exists():
        meta.unlink()
        deleted = True
    return deleted


def get_session_meta(user_slug: str, channel: str, session_id: str) -> SessionMeta | None:
    """Return metadata for a session, or None if not found."""
    return _load_meta(user_slug, channel, session_id)


# ---------------------------------------------------------------------------
# Message read/write — per-session files with legacy fallback
# ---------------------------------------------------------------------------


def append_message(user_slug: str, message: HistoryMessage, channel: str = 'default') -> None:
    """Append a message to the session's JSONL file.

    Uses ``message.conversation_id`` as the session ID.
    Creates the session directory and meta file if needed.

    Args:
        user_slug: The user's slug.
        message: The message to append.
        channel: The originating channel (used for directory placement).
    """
    session_id = message.conversation_id

    # Resolve channel: check if session already exists in some channel dir
    resolved_channel = _resolve_channel(user_slug, session_id) or channel

    path = _session_path(user_slug, resolved_channel, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = message.to_jsonl() + '\n'
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line)

    _touch_meta(user_slug, resolved_channel, session_id)


def _read_session_file(path: Path) -> list[HistoryMessage]:
    """Read all messages from a single JSONL file."""
    if not path.exists():
        return []
    messages: list[HistoryMessage] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(HistoryMessage.from_jsonl(line))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.warning('Failed to parse history line: %s — %s', line[:100], exc)
    return messages


def _read_legacy_history(
    user_slug: str,
    conversation_id: str | None = None,
) -> list[HistoryMessage]:
    """Read from the legacy flat history.jsonl, filtered by conversation_id."""
    path = _legacy_history_path(user_slug)
    if not path.exists():
        return []
    messages: list[HistoryMessage] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = HistoryMessage.from_jsonl(line)
                if conversation_id is None or msg.conversation_id == conversation_id:
                    messages.append(msg)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.warning('Failed to parse legacy history line: %s — %s', line[:100], exc)
    return messages


def read_history(
    user_slug: str,
    conversation_id: str | None = None,
    limit: int | None = None,
) -> list[HistoryMessage]:
    """Read history messages for a user, optionally filtered by conversation.

    Tries per-session files first, falls back to legacy flat file.

    Args:
        user_slug: The user's slug.
        conversation_id: If provided, only return messages from this conversation.
        limit: Maximum number of messages to return (most recent first).

    Returns:
        List of messages, newest first if limit is provided, otherwise chronological order.
    """
    messages: list[HistoryMessage] = []

    if conversation_id:
        # Try per-session file first
        channel = _resolve_channel(user_slug, conversation_id)
        if channel:
            path = _session_path(user_slug, channel, conversation_id)
            messages = _read_session_file(path)
        else:
            # Fall back to legacy flat file
            messages = _read_legacy_history(user_slug, conversation_id)
    else:
        # Read all sessions — scan all channel dirs
        sessions_root = _sessions_dir(user_slug)
        if sessions_root.exists():
            for channel_dir in sorted(sessions_root.iterdir()):
                if not channel_dir.is_dir():
                    continue
                for jsonl_file in sorted(channel_dir.glob('*.jsonl')):
                    messages.extend(_read_session_file(jsonl_file))
        if not messages:
            # Fall back to legacy
            messages = _read_legacy_history(user_slug)
        messages.sort(key=lambda m: m.timestamp)

    if limit is not None and len(messages) > limit:
        messages = messages[-limit:]

    return messages


def read_recent_turns(user_slug: str, conversation_id: str, num_turns: int = 10) -> list[HistoryMessage]:
    """Read the most recent N turns (user + assistant pairs) for a conversation.

    Args:
        user_slug: The user's slug.
        conversation_id: The conversation identifier.
        num_turns: Number of turns to retrieve (default: 10).

    Returns:
        List of messages from the last N turns, chronological order.
    """
    messages = read_history(user_slug, conversation_id=conversation_id)

    # Count turns (user messages start turns)
    turn_starts: list[int] = []
    for i, msg in enumerate(messages):
        if msg.role == 'user':
            turn_starts.append(i)

    # Take last N turn starts
    if len(turn_starts) > num_turns:
        start_idx = turn_starts[-num_turns]
        messages = messages[start_idx:]

    return messages


def count_tokens_estimate(messages: list[HistoryMessage]) -> int:
    """Estimate token count for a list of messages.

    Uses a simple heuristic: ~4 characters per token.
    """
    total_chars = 0
    for msg in messages:
        if msg.text:
            total_chars += len(msg.text)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total_chars += len(tc.name) + len(json.dumps(tc.arguments))
    return total_chars // 4


def create_compaction_summary(
    messages: list[HistoryMessage], summary_text: str, conversation_id: str
) -> HistoryMessage:
    """Create a synthetic system message representing a compaction summary."""
    return HistoryMessage(
        role='system',
        text=f'[Context summary: {summary_text}]',
        timestamp=datetime.now(tz=timezone.utc),
        conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# Migration utility
# ---------------------------------------------------------------------------


def migrate_legacy_history(user_slug: str, default_channel: str = 'default') -> int:
    """Split a legacy flat history.jsonl into per-session files.

    Reads the legacy file, groups messages by conversation_id, and writes
    each group to its own session file with metadata.

    Args:
        user_slug: The user's slug.
        default_channel: Channel to assign to sessions (since the legacy file
                         doesn't track channel per message).

    Returns:
        Number of sessions migrated.
    """
    legacy_path = _legacy_history_path(user_slug)
    if not legacy_path.exists():
        return 0

    messages = _read_legacy_history(user_slug)
    if not messages:
        return 0

    # Group by conversation_id
    sessions: dict[str, list[HistoryMessage]] = {}
    for msg in messages:
        sessions.setdefault(msg.conversation_id, []).append(msg)

    count = 0
    for session_id, session_messages in sessions.items():
        # Skip if already migrated
        if _resolve_channel(user_slug, session_id):
            continue

        # Create session directory and files
        path = _session_path(user_slug, default_channel, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            for msg in session_messages:
                f.write(msg.to_jsonl() + '\n')

        # Create metadata
        first_ts = session_messages[0].timestamp
        last_ts = session_messages[-1].timestamp
        meta = SessionMeta(
            session_id=session_id,
            channel=default_channel,
            created_at=first_ts,
            last_active=last_ts,
            message_count=len(session_messages),
        )
        _save_meta(user_slug, default_channel, meta)
        count += 1

    # Rename legacy file to mark as migrated
    if count > 0:
        legacy_path.rename(legacy_path.with_suffix('.jsonl.migrated'))
        log.info('Migrated %d sessions from legacy history for user %s', count, user_slug)

    return count
