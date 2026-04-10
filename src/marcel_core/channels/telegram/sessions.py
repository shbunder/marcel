"""Telegram session state: maps chat IDs to Marcel users and active conversations.

User linking is stored per-user in ``data/users/{slug}/telegram.json``::

    {"chat_id": "556632386"}

This keeps Telegram config with the rest of the user's data. To link a user,
call :func:`link_user`. Session state is persisted per-chat in
``data/telegram/sessions.json`` so conversation context survives server restarts.

Session state per chat::

    {
        "conversation_id": "2026-03-29T14-00",
        "last_message_at": "2026-03-29T14:32:00"
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel

from marcel_core.storage._atomic import atomic_write
from marcel_core.storage._root import data_root

# Hours of inactivity after which a new conversation is started automatically.
# Set high (48h) so Telegram feels like one continuous conversation.
# New sessions are created explicitly via /new or on Marcel restart.
AUTO_NEW_HOURS = 48


class SessionState(BaseModel):
    """Per-chat Telegram session state."""

    conversation_id: str | None = None
    last_message_at: str | None = None


def _sessions_path():
    return data_root() / 'telegram' / 'sessions.json'


def _load_sessions() -> dict[str, SessionState]:
    """Load ``{chat_id: SessionState}`` from the sessions file."""
    path = _sessions_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}

    # Migrate legacy formats to current SessionState
    sessions: dict[str, SessionState] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            sessions[key] = SessionState(conversation_id=value)
        elif isinstance(value, dict):
            sessions[key] = SessionState.model_validate(value)
        # Skip malformed entries
    return sessions


def _save_sessions(sessions: dict[str, SessionState]) -> None:
    serialized = {k: v.model_dump(exclude_none=True) for k, v in sessions.items()}
    atomic_write(_sessions_path(), json.dumps(serialized, indent=2))


def _get_state(chat_id: int | str) -> SessionState:
    """Return the session state for a chat, or a default if none exists."""
    return _load_sessions().get(str(chat_id), SessionState())


def _update_state(chat_id: int | str, **updates: object) -> SessionState:
    """Merge *updates* into the session state for *chat_id* and persist."""
    sessions = _load_sessions()
    key = str(chat_id)
    state = sessions.get(key, SessionState())
    # Apply updates to the model
    for field_name, value in updates.items():
        setattr(state, field_name, value)
    sessions[key] = state
    _save_sessions(sessions)
    return state


# ---------------------------------------------------------------------------
# User linking
# ---------------------------------------------------------------------------


def link_user(user_slug: str, chat_id: int | str) -> None:
    """Link a Marcel user to a Telegram chat ID.

    Writes ``data/users/{user_slug}/telegram.json`` with the chat ID.
    Creates the user directory if it does not yet exist.
    """
    path = data_root() / 'users' / user_slug / 'telegram.json'
    atomic_write(path, json.dumps({'chat_id': str(chat_id)}, indent=2))


def get_user_slug(chat_id: int | str) -> str | None:
    """Return the Marcel user slug for a Telegram chat ID, or None if not linked."""
    target = str(chat_id)
    users_dir = data_root() / 'users'
    if not users_dir.exists():
        return None
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        telegram_file = user_dir / 'telegram.json'
        if not telegram_file.exists():
            continue
        try:
            data = json.loads(telegram_file.read_text(encoding='utf-8'))
            if str(data.get('chat_id', '')) == target:
                return user_dir.name
        except (json.JSONDecodeError, OSError):
            continue
    return None


def get_chat_id(user_slug: str) -> str | None:
    """Return the Telegram chat ID for a Marcel user slug, or None if not linked."""
    telegram_file = data_root() / 'users' / user_slug / 'telegram.json'
    try:
        data = json.loads(telegram_file.read_text(encoding='utf-8'))
        return str(data.get('chat_id', '')) or None
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------


def get_conversation_id(chat_id: int | str) -> str | None:
    """Return the active conversation ID for a chat, or None if none exists."""
    return _get_state(chat_id).conversation_id


def set_conversation_id(chat_id: int | str, conversation_id: str) -> None:
    """Persist the active conversation ID for a chat."""
    _update_state(chat_id, conversation_id=conversation_id)


def touch_last_message(chat_id: int | str) -> None:
    """Update the last-message timestamp for a chat to now (UTC)."""
    _update_state(chat_id, last_message_at=datetime.now(timezone.utc).isoformat())


def should_auto_new(chat_id: int | str) -> bool:
    """Return True if the chat has been inactive for longer than AUTO_NEW_HOURS."""
    last = _get_state(chat_id).last_message_at
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    elapsed = datetime.now(timezone.utc) - last_dt
    return elapsed.total_seconds() > AUTO_NEW_HOURS * 3600


def reset_session(chat_id: int | str) -> None:
    """Clear conversation — used by /new command."""
    _update_state(chat_id, conversation_id=None)


def clear_all_sessions() -> None:
    """Clear all conversation IDs — called on Marcel startup.

    Preserves user linking (chat_id → user_slug) but resets conversation
    state so every user starts a fresh session after a restart.
    """
    sessions = _load_sessions()
    for state in sessions.values():
        state.conversation_id = None
    _save_sessions(sessions)
