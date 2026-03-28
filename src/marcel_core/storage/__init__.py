"""Flat-file storage layer for Marcel.

All read/write operations for users, conversations, and distilled memory.
Files are plain markdown; no database is required.

Public API
----------
Users:
    :func:`user_exists`, :func:`load_user_profile`, :func:`save_user_profile`

Conversations:
    :func:`new_conversation`, :func:`append_turn`, :func:`load_conversation`,
    :func:`load_conversation_index`, :func:`update_conversation_index`

Memory:
    :func:`load_memory_index`, :func:`load_memory_file`,
    :func:`save_memory_file`, :func:`update_memory_index`

Concurrency helpers:
    :func:`get_lock` — per-user ``asyncio.Lock`` for the API layer.
"""

from ._locks import get_lock
from .conversations import (
    append_turn,
    load_conversation,
    load_conversation_index,
    new_conversation,
    update_conversation_index,
)
from .memory import (
    load_memory_file,
    load_memory_index,
    save_memory_file,
    update_memory_index,
)
from .users import load_user_profile, save_user_profile, user_exists

__all__ = [
    # users
    'user_exists',
    'load_user_profile',
    'save_user_profile',
    # conversations
    'new_conversation',
    'append_turn',
    'load_conversation',
    'load_conversation_index',
    'update_conversation_index',
    # memory
    'load_memory_index',
    'load_memory_file',
    'save_memory_file',
    'update_memory_index',
    # concurrency
    'get_lock',
]
