"""Flat-file storage layer for Marcel.

All read/write operations for users and distilled memory.
Files are plain markdown; no database is required.

Conversation history is managed by :mod:`marcel_core.memory.history` (JSONL
session files), not by this module.

Public API
----------
Users:
    :func:`user_exists`, :func:`load_user_profile`, :func:`save_user_profile`

Memory:
    :func:`load_memory_file`, :func:`save_memory_file`

Concurrency helpers:
    :func:`get_lock` — per-user ``asyncio.Lock`` for the API layer.
"""

from ._locks import get_lock
from .memory import (
    MemoryHeader,
    MemorySearchResult,
    MemoryType,
    format_memory_manifest,
    human_age,
    load_memory_file,
    memory_age_days,
    memory_freshness_note,
    parse_frontmatter,
    prune_expired_memories,
    save_memory_file,
    scan_memory_headers,
    search_memory_files,
)
from .users import load_user_profile, save_user_profile, user_exists

__all__ = [
    # users
    'user_exists',
    'load_user_profile',
    'save_user_profile',
    # memory
    'MemoryHeader',
    'MemorySearchResult',
    'MemoryType',
    'format_memory_manifest',
    'human_age',
    'load_memory_file',
    'memory_age_days',
    'memory_freshness_note',
    'parse_frontmatter',
    'prune_expired_memories',
    'save_memory_file',
    'scan_memory_headers',
    'search_memory_files',
    # concurrency
    'get_lock',
]
