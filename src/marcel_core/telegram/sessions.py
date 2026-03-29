"""Telegram session state: maps chat IDs to Marcel users and active conversations.

User linking is stored per-user in ``data/users/{slug}/telegram.json``::

    {"chat_id": "556632386"}

This keeps Telegram config with the rest of the user's data. To link a user,
call :func:`link_user`. Session state is persisted per-chat in
``data/telegram/sessions.json`` so conversation context survives server restarts.

Session state per chat::

    {
        "conversation_id": "2026-03-29T14-00",
        "mode": "assistant",
        "coder_session_id": null,
        "last_message_at": "2026-03-29T14:32:00"
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TypedDict

from marcel_core.storage._atomic import atomic_write
from marcel_core.storage._root import data_root

# Hours of inactivity after which a new conversation is started automatically.
AUTO_NEW_HOURS = 6


class SessionState(TypedDict, total=False):
    conversation_id: str | None
    mode: str  # "assistant" | "coder"
    coder_session_id: str | None
    last_message_at: str | None


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

    # Migrate legacy format: {chat_id: "conversation_id"} → SessionState
    sessions: dict[str, SessionState] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            sessions[key] = SessionState(
                conversation_id=value,
                mode='assistant',
                coder_session_id=None,
                last_message_at=None,
            )
        elif isinstance(value, dict):
            sessions[key] = value  # type: ignore[assignment]
        # Skip malformed entries
    return sessions


def _save_sessions(sessions: dict[str, SessionState]) -> None:
    atomic_write(_sessions_path(), json.dumps(sessions, indent=2))


def _get_state(chat_id: int | str) -> SessionState:
    """Return the session state for a chat, or a default if none exists."""
    return _load_sessions().get(
        str(chat_id),
        SessionState(
            conversation_id=None,
            mode='assistant',
            coder_session_id=None,
            last_message_at=None,
        ),
    )


def _update_state(chat_id: int | str, **updates: object) -> SessionState:
    """Merge *updates* into the session state for *chat_id* and persist."""
    sessions = _load_sessions()
    key = str(chat_id)
    state = sessions.get(
        key,
        SessionState(
            conversation_id=None,
            mode='assistant',
            coder_session_id=None,
            last_message_at=None,
        ),
    )
    state.update(updates)  # type: ignore[typeddict-item]
    sessions[key] = state
    _save_sessions(sessions)
    return state


# ---------------------------------------------------------------------------
# User linking (unchanged)
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
# Conversation & mode state
# ---------------------------------------------------------------------------


def get_conversation_id(chat_id: int | str) -> str | None:
    """Return the active conversation ID for a chat, or None if none exists."""
    return _get_state(chat_id).get('conversation_id')


def set_conversation_id(chat_id: int | str, conversation_id: str) -> None:
    """Persist the active conversation ID for a chat."""
    _update_state(chat_id, conversation_id=conversation_id)


def get_mode(chat_id: int | str) -> str:
    """Return the current mode for a chat: ``"assistant"`` or ``"coder"``."""
    return _get_state(chat_id).get('mode', 'assistant')


def get_coder_session_id(chat_id: int | str) -> str | None:
    """Return the Claude Code session ID for an active coder session."""
    return _get_state(chat_id).get('coder_session_id')


def enter_coder_mode(chat_id: int | str, coder_session_id: str | None = None) -> None:
    """Switch a chat into coder mode, optionally storing a session ID."""
    _update_state(chat_id, mode='coder', coder_session_id=coder_session_id)


def exit_coder_mode(chat_id: int | str) -> None:
    """Switch a chat back to assistant mode and clear the coder session."""
    _update_state(chat_id, mode='assistant', coder_session_id=None)


def set_coder_session_id(chat_id: int | str, session_id: str) -> None:
    """Store the Claude Code session ID captured from a StreamEvent."""
    _update_state(chat_id, coder_session_id=session_id)


def touch_last_message(chat_id: int | str) -> None:
    """Update the last-message timestamp for a chat to now (UTC)."""
    _update_state(chat_id, last_message_at=datetime.now(timezone.utc).isoformat())


def should_auto_new(chat_id: int | str) -> bool:
    """Return True if the chat has been inactive for longer than AUTO_NEW_HOURS."""
    last = _get_state(chat_id).get('last_message_at')
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    elapsed = datetime.now(timezone.utc) - last_dt
    return elapsed.total_seconds() > AUTO_NEW_HOURS * 3600


def reset_session(chat_id: int | str) -> None:
    """Clear conversation, exit coder mode — used by /new command."""
    _update_state(
        chat_id,
        conversation_id=None,
        mode='assistant',
        coder_session_id=None,
    )
