"""Conversation storage: create, append, load, and index conversations."""

import pathlib
from datetime import datetime, timezone

from ._atomic import atomic_write
from ._root import data_root


def _conv_dir(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug / 'conversations'


def _conv_path(slug: str, filename: str) -> pathlib.Path:
    return _conv_dir(slug) / f'{filename}.md'


def _index_path(slug: str) -> pathlib.Path:
    return _conv_dir(slug) / 'index.md'


def new_conversation(slug: str, channel: str) -> str:
    """
    Create a new conversation file and return its filename stem.

    The filename stem uses the format ``YYYY-MM-DDTHH-MM`` (colons replaced
    with dashes for filesystem safety).  The file header uses the display
    format ``YYYY-MM-DDTHH:MM``.

    Args:
        slug: The user's short identifier.
        channel: The originating channel (e.g. ``"cli"``, ``"telegram"``).

    Returns:
        The filename stem, e.g. ``"2026-03-26T14-32"``.
    """
    now = datetime.now(tz=timezone.utc)
    display_ts = now.strftime('%Y-%m-%dT%H:%M')
    file_stem = now.strftime('%Y-%m-%dT%H-%M')
    header = f'# Conversation — {display_ts} (channel: {channel})\n'
    path = _conv_path(slug, file_stem)
    atomic_write(path, header)
    return file_stem


def append_turn(slug: str, filename: str, role: str, text: str) -> None:
    """
    Append a single turn to an existing conversation file.

    Args:
        slug: The user's short identifier.
        filename: The conversation filename stem (without ``.md``).
        role: Either ``"user"`` or ``"assistant"`` — displayed as ``User`` or
            ``Marcel`` respectively.
        text: The message text to append.
    """
    path = _conv_path(slug, filename)
    display_role = 'Marcel' if role == 'assistant' else role.capitalize()
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    # Ensure there is a blank line before each turn.
    separator = '\n' if existing.endswith('\n\n') else '\n\n' if existing else ''
    updated = existing + separator + f'**{display_role}:** {text}\n'
    atomic_write(path, updated)


def load_conversation(slug: str, filename: str) -> str:
    """
    Return the raw markdown content of a conversation file.

    Args:
        slug: The user's short identifier.
        filename: The conversation filename stem (without ``.md``).

    Returns:
        Raw markdown, or an empty string if the file does not exist.
    """
    path = _conv_path(slug, filename)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def load_conversation_index(slug: str) -> str:
    """
    Return the raw markdown content of the conversation index.

    Args:
        slug: The user's short identifier.

    Returns:
        Raw markdown of ``conversations/index.md``, or an empty string if it
        does not exist.
    """
    path = _index_path(slug)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def update_conversation_index(slug: str, filename: str, description: str) -> None:
    """
    Append a new entry to the conversation index.

    Creates the index file if it does not already exist.

    Args:
        slug: The user's short identifier.
        filename: The conversation filename stem (without ``.md``).
        description: A short human-readable summary of the conversation.
    """
    path = _index_path(slug)
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    entry = f'- [{filename}]({filename}.md) — {description}\n'
    updated = existing + entry
    atomic_write(path, updated)
