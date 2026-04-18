"""Per-user filesystem path helpers used by the plugin surface.

Public, named helpers — :mod:`marcel_core.plugin.paths` re-exports these so
external integration habitats never have to know the data-root layout.

The data root itself is owned by :mod:`marcel_core.storage._root` and resolves
to ``settings.data_dir`` (default ``~/.marcel/``). Habitats should not import
that module directly; they reach for these helpers instead.
"""

from __future__ import annotations

import pathlib

from ._root import data_root


def user_dir(slug: str) -> pathlib.Path:
    """Return the per-user data directory for *slug*.

    Resolves to ``<data_root>/users/{slug}/``. The directory is **not** created
    here — callers that need to write should ``mkdir(parents=True, exist_ok=True)``
    on the specific subpath they want, so an empty user directory is never
    created as a side effect of a read-style call.
    """
    return data_root() / 'users' / slug


def cache_dir(slug: str) -> pathlib.Path:
    """Return (and create) the per-user cache directory for *slug*.

    Resolves to ``<data_root>/users/{slug}/cache/``. The directory is created
    if missing — every observed caller writes to it immediately, so eager
    creation removes a boilerplate ``mkdir`` from each habitat.
    """
    path = user_dir(slug) / 'cache'
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_user_slugs() -> list[str]:
    """Return the slugs of every user with a directory under ``<data_root>/users/``.

    Returns an empty list when no users directory exists. The order is the
    filesystem's iteration order — callers that need a stable order should
    sort the result.
    """
    users_root = data_root() / 'users'
    if not users_root.is_dir():
        return []
    return [entry.name for entry in users_root.iterdir() if entry.is_dir()]
