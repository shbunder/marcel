"""User onboarding + offboarding (FEAT-260707-6130cd, STORY-6286e0).

``add_user`` is the single onboarding path — it creates the per-user
directory, a ``profile.md`` with the role frontmatter, and the memory
directory, idempotently. ``remove_user`` offboards by **archiving** the
directory (moved under ``<data>/archive/users/``, never deleted — Core
principle: Recoverable) and unlinking any channel sessions.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime

log = logging.getLogger(__name__)

_VALID_ROLES = ('admin', 'user')


class UserOpError(ValueError):
    """A user op could not proceed — message is admin-readable."""


def _validated_slug(slug: str) -> str:
    from marcel_core.auth import valid_user_slug

    if not valid_user_slug(slug):
        raise UserOpError(f'Invalid user slug {slug!r} — use lowercase letters, digits, - or _.')
    return slug


def add_user(slug: str, role: str = 'user') -> str:
    """Create (or top up) a user: directory, ``profile.md`` with role, memory dir.

    Idempotent — an existing user keeps their profile body; only a missing
    role or missing directories are filled in.
    """
    if role not in _VALID_ROLES:
        raise UserOpError(f'Invalid role {role!r} — must be admin or user.')
    slug = _validated_slug(slug)

    from marcel_core.storage.paths import user_dir
    from marcel_core.storage.users import get_user_role, set_user_role

    udir = user_dir(slug)
    existed = udir.exists()
    (udir / 'memory').mkdir(parents=True, exist_ok=True)

    profile = udir / 'profile.md'
    if not profile.exists():
        profile.write_text(f'---\nrole: {role}\n---\n\n# {slug.capitalize()}\n', encoding='utf-8')
    elif get_user_role(slug) != role:
        # Existing profile without the requested role — set it, preserving body.
        set_user_role(slug, role)

    where = 'updated' if existed else 'created'
    return f"User '{slug}' {where} (role: {get_user_role(slug)}) at {udir}."


def remove_user(slug: str) -> str:
    """Offboard a user by archiving their directory — never deleting it.

    Moves ``<data>/users/<slug>`` to ``<data>/archive/users/<slug>-<utc>`` and
    unlinks any channel sessions, so the data is recoverable and no other user
    can be affected.
    """
    slug = _validated_slug(slug)

    from marcel_core.storage._root import data_root
    from marcel_core.storage.paths import user_dir

    udir = user_dir(slug)
    if not udir.is_dir():
        raise UserOpError(f"No user '{slug}' to remove (nothing at {udir}).")

    _unlink_channels(slug)

    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    archive = data_root() / 'archive' / 'users' / f'{slug}-{stamp}'
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(udir), str(archive))
    return f"User '{slug}' archived to {archive} (not deleted — recoverable). Channel links unlinked."


def _unlink_channels(slug: str) -> None:
    """Best-effort unlink of a user's channel sessions (e.g. Telegram)."""
    try:
        from marcel_core.plugin.channels import discover, get_channel, list_channels

        discover()
        for name in list_channels():
            plugin = get_channel(name)
            unlink = getattr(plugin, 'unlink_user', None)
            if callable(unlink):
                unlink(slug)
    except Exception:
        log.debug('remove_user: channel unlink skipped for %s', slug, exc_info=True)
