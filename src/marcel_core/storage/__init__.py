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
    :func:`load_memory_index`, :func:`load_memory_file`,
    :func:`save_memory_file`, :func:`update_memory_index`

Concurrency helpers:
    :func:`get_lock` — per-user ``asyncio.Lock`` for the API layer.
"""

from ._locks import get_lock
from .memory import (
    MemoryHeader,
    MemorySearchResult,
    MemoryType,
    enforce_index_cap,
    format_memory_index,
    format_memory_manifest,
    human_age,
    load_memory_file,
    load_memory_index,
    memory_age_days,
    memory_freshness_note,
    parse_frontmatter,
    prune_expired_memories,
    rebuild_memory_index,
    save_memory_file,
    scan_memory_headers,
    search_memory_files,
    update_memory_index,
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
    'enforce_index_cap',
    'format_memory_index',
    'format_memory_manifest',
    'human_age',
    'load_memory_index',
    'load_memory_file',
    'memory_age_days',
    'memory_freshness_note',
    'parse_frontmatter',
    'prune_expired_memories',
    'rebuild_memory_index',
    'save_memory_file',
    'scan_memory_headers',
    'search_memory_files',
    'update_memory_index',
    # concurrency
    'get_lock',
]
