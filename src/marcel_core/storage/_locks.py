"""Per-user asyncio.Lock registry.

Storage functions are synchronous, but the API layer can acquire a per-user
lock before calling them to prevent concurrent write races from multiple
channels.
"""

import asyncio

_locks: dict[str, asyncio.Lock] = {}


def get_lock(slug: str) -> asyncio.Lock:
    """Return the asyncio.Lock for the given user slug, creating it if needed.

    Args:
        slug: The user's short identifier (e.g. ``"shaun"``).

    Returns:
        An ``asyncio.Lock`` instance unique to this slug.
    """
    if slug not in _locks:
        _locks[slug] = asyncio.Lock()
    return _locks[slug]
