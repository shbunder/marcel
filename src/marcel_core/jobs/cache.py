"""Simple file-based cache for inter-job data sharing.

Jobs can write structured data to a named cache key and other jobs can
read it later.  Stored per-user at ``<data_root>/users/<slug>/job_cache/<key>.json``.

Each cache entry includes a timestamp so consumers know how fresh the data is.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)


def _cache_dir(user_slug: str) -> Path:
    d = data_root() / 'users' / user_slug / 'job_cache'
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_cache(user_slug: str, key: str, data: Any) -> None:
    """Write data to a named cache key.

    Args:
        user_slug: The user who owns this cache entry.
        key: Cache key name (e.g. ``"news"``, ``"bank_summary"``).
        data: JSON-serializable data to store.
    """
    entry = {
        'key': key,
        'updated_at': datetime.now(UTC).isoformat(),
        'data': data,
    }
    path = _cache_dir(user_slug) / f'{key}.json'
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('[job-cache] Wrote key=%s for user=%s (%d bytes)', key, user_slug, path.stat().st_size)


def read_cache(user_slug: str, key: str) -> dict[str, Any] | None:
    """Read a cache entry by key.

    Returns:
        Dict with ``key``, ``updated_at``, and ``data`` fields, or *None* if
        the key doesn't exist.
    """
    path = _cache_dir(user_slug) / f'{key}.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        log.warning('[job-cache] Failed to read key=%s for user=%s', key, user_slug)
        return None


def list_cache_keys(user_slug: str) -> list[str]:
    """Return all cache keys for a user."""
    d = _cache_dir(user_slug)
    return sorted(p.stem for p in d.glob('*.json'))
