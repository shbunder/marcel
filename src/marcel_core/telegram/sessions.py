"""Telegram session state: maps chat IDs to Marcel users and active conversations.

User linking is stored per-user in ``data/users/{slug}/telegram.json``::

    {"chat_id": "556632386"}

This keeps Telegram config with the rest of the user's data. To link a user,
call :func:`link_user`. Active conversation IDs are persisted per-chat in
``data/telegram/sessions.json`` so conversation context survives server restarts.
"""

import json

from marcel_core.storage._atomic import atomic_write
from marcel_core.storage._root import data_root


def _sessions_path():
    return data_root() / 'telegram' / 'sessions.json'


def _load_sessions() -> dict[str, str | None]:
    """Load ``{chat_id: conversation_id}`` from the sessions file."""
    path = _sessions_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sessions(sessions: dict[str, str | None]) -> None:
    atomic_write(_sessions_path(), json.dumps(sessions, indent=2))


def link_user(user_slug: str, chat_id: int | str) -> None:
    """Link a Marcel user to a Telegram chat ID.

    Writes ``data/users/{user_slug}/telegram.json`` with the chat ID.
    Creates the user directory if it does not yet exist.

    Args:
        user_slug: The Marcel user slug (directory name under ``data/users/``).
        chat_id: The Telegram chat or user ID to associate with this user.
    """
    path = data_root() / 'users' / user_slug / 'telegram.json'
    atomic_write(path, json.dumps({'chat_id': str(chat_id)}, indent=2))


def get_user_slug(chat_id: int | str) -> str | None:
    """Return the Marcel user slug for a Telegram chat ID, or None if not linked.

    Scans ``data/users/*/telegram.json`` files for a matching chat ID.
    The number of users is expected to be small (household scale).

    Args:
        chat_id: The Telegram chat or user ID to look up.

    Returns:
        The Marcel user slug, or None if no user is linked to this chat ID.
    """
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


def get_conversation_id(chat_id: int | str) -> str | None:
    """Return the active conversation ID for a chat, or None if none exists.

    Args:
        chat_id: The Telegram chat or user ID.

    Returns:
        A conversation ID string (filename stem), or None.
    """
    return _load_sessions().get(str(chat_id))


def set_conversation_id(chat_id: int | str, conversation_id: str) -> None:
    """Persist the active conversation ID for a chat.

    Args:
        chat_id: The Telegram chat or user ID.
        conversation_id: The conversation ID to associate with this chat.
    """
    sessions = _load_sessions()
    sessions[str(chat_id)] = conversation_id
    _save_sessions(sessions)
