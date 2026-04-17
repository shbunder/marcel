"""User profile storage: existence check, load, save, and role management.

The user profile lives at ``data/users/{slug}/profile.md`` with YAML
frontmatter for structured metadata (role, channel links) and a markdown
body for identity/preferences injected into the system prompt::

    ---
    role: admin
    telegram_chat_id: "556632386"
    ---

    # Shaun
    ...
"""

import pathlib
import re
from typing import Literal

from ._atomic import atomic_write
from ._root import data_root

UserRole = Literal['admin', 'user']

_VALID_ROLES: frozenset[str] = frozenset({'admin', 'user'})

_FRONTMATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_KV_RE = re.compile(r'^(\w+)\s*:\s*(.+)$', re.MULTILINE)


def _user_dir(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug


_BACKUP_SLUG_RE = re.compile(r'\.backup-\d')


def is_backup_slug(slug: str) -> bool:
    """Return True if ``slug`` is a user-data backup snapshot, not a live user.

    The backup naming convention ``{base}.backup-{issue-num}-{timestamp}``
    was introduced by the ISSUE-059 migration and kept for any subsequent
    data moves. Backup dirs are preserved so a migration can be rolled back,
    but they are **not** live users — the scheduler, memory consolidator,
    and Telegram lookup must skip them.
    """
    return bool(_BACKUP_SLUG_RE.search(slug))


def user_exists(slug: str) -> bool:
    """
    Return True if the user directory exists.

    Args:
        slug: The user's short identifier (e.g. ``"shaun"``).

    Returns:
        ``True`` if ``data/users/{slug}/`` exists, ``False`` otherwise.
    """
    return _user_dir(slug).is_dir()


# ---------------------------------------------------------------------------
# Profile frontmatter helpers
# ---------------------------------------------------------------------------


def _parse_profile(text: str) -> tuple[dict[str, str], str]:
    """Parse profile.md into (frontmatter dict, body text).

    Returns ({}, full text) when no frontmatter is present.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw_fm = match.group(1)
    body = text[match.end() :]
    metadata = {m.group(1): m.group(2).strip().strip('"').strip("'") for m in _KV_RE.finditer(raw_fm)}
    return metadata, body


def _serialize_profile(metadata: dict[str, str], body: str) -> str:
    """Serialize frontmatter dict + body back to profile.md format."""
    if not metadata:
        return body
    lines = ['---']
    for key, value in metadata.items():
        # Quote values that contain special chars
        if any(c in str(value) for c in ' :#[]{}'):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f'{key}: {value}')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines) + body


def _read_profile_raw(slug: str) -> str:
    """Read the raw profile.md content, or empty string if missing."""
    path = _user_dir(slug) / 'profile.md'
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def _write_profile_raw(slug: str, content: str) -> None:
    """Write raw content to profile.md."""
    path = _user_dir(slug) / 'profile.md'
    atomic_write(path, content)


def _read_profile_meta(slug: str) -> tuple[dict[str, str], str]:
    """Read and parse profile.md, returning (metadata, body)."""
    return _parse_profile(_read_profile_raw(slug))


def _update_profile_field(slug: str, key: str, value: str) -> None:
    """Update a single frontmatter field in profile.md, preserving the body."""
    metadata, body = _read_profile_meta(slug)
    metadata[key] = value
    _write_profile_raw(slug, _serialize_profile(metadata, body))


# ---------------------------------------------------------------------------
# Public API — profile load/save (body only, for system prompt injection)
# ---------------------------------------------------------------------------


def load_user_profile(slug: str) -> str:
    """
    Load the user's profile markdown body (without frontmatter).

    Args:
        slug: The user's short identifier.

    Returns:
        Markdown body of ``profile.md``, or an empty string if the file
        does not exist.
    """
    _, body = _read_profile_meta(slug)
    return body


def save_user_profile(slug: str, content: str) -> None:
    """
    Persist the user's profile markdown, preserving existing frontmatter.

    Creates the user directory if it does not already exist.

    Args:
        slug: The user's short identifier.
        content: Raw markdown body to write to ``profile.md``.
    """
    metadata, _ = _read_profile_meta(slug)
    _write_profile_raw(slug, _serialize_profile(metadata, content))


# ---------------------------------------------------------------------------
# Role management (stored in profile.md frontmatter)
# ---------------------------------------------------------------------------


def get_user_role(slug: str) -> str:
    """
    Return the user's role: ``'admin'`` or ``'user'`` (default).

    Reads the ``role`` field from ``profile.md`` frontmatter. If the file
    is absent or the role field is missing/invalid, returns ``'user'``.

    Args:
        slug: The user's short identifier.

    Returns:
        ``'admin'`` or ``'user'``.
    """
    metadata, _ = _read_profile_meta(slug)
    role = metadata.get('role', 'user')
    if role not in _VALID_ROLES:
        return 'user'
    return role


def set_user_role(slug: str, role: str) -> None:
    """
    Persist the user's role in profile.md frontmatter.

    Creates the user directory if it does not already exist.

    Args:
        slug: The user's short identifier.
        role: Must be ``'admin'`` or ``'user'``.

    Raises:
        ValueError: If ``role`` is not a valid role.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role {role!r}. Must be 'admin' or 'user'.")
    _update_profile_field(slug, 'role', role)


# ---------------------------------------------------------------------------
# Telegram chat ID (stored in profile.md frontmatter)
# ---------------------------------------------------------------------------


def get_telegram_chat_id(slug: str) -> str | None:
    """Return the Telegram chat ID for a user, or None if not linked."""
    metadata, _ = _read_profile_meta(slug)
    return metadata.get('telegram_chat_id') or None


def set_telegram_chat_id(slug: str, chat_id: str) -> None:
    """Store the Telegram chat ID in profile.md frontmatter."""
    _update_profile_field(slug, 'telegram_chat_id', str(chat_id))


def find_user_by_telegram_chat_id(chat_id: int | str) -> str | None:
    """Return the user slug for a Telegram chat ID, or None if not linked."""
    target = str(chat_id)
    users_dir = data_root() / 'users'
    if not users_dir.exists():
        return None
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        if is_backup_slug(user_dir.name):
            continue
        profile_path = user_dir / 'profile.md'
        if not profile_path.exists():
            continue
        try:
            raw = profile_path.read_text(encoding='utf-8')
            metadata, _ = _parse_profile(raw)
            if metadata.get('telegram_chat_id') == target:
                return user_dir.name
        except OSError:
            continue
    return None
