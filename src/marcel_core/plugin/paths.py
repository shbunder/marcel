"""Per-user filesystem path helpers for plugin habitats.

Habitats need to write per-user state (caches, downloaded files, key material)
without knowing where the data root lives or how it is laid out. These helpers
expose the bare minimum: where one user's data goes, where one user's caches
go, and which users currently exist on disk.

This module is part of the stable plugin surface — the function names here
won't change between Marcel versions without a migration note. Habitats
should import from here, not from ``marcel_core.storage.paths``.

Example::

    from marcel_core.plugin import paths

    cache_file = paths.cache_dir(user_slug) / 'mything.db'
    pem_file = paths.user_dir(user_slug) / 'signing_key.pem'
    for slug in paths.list_user_slugs():
        ...
"""

from __future__ import annotations

from marcel_core.storage.paths import cache_dir, list_user_slugs, user_dir

__all__ = ['cache_dir', 'list_user_slugs', 'user_dir']
