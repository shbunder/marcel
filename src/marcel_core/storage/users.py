"""User profile storage: existence check, load, save, and role management."""

import json
import pathlib
from typing import Literal

from pydantic import BaseModel, field_validator

from ._atomic import atomic_write
from ._root import data_root

UserRole = Literal['admin', 'user']

_VALID_ROLES: frozenset[str] = frozenset({'admin', 'user'})


class UserMeta(BaseModel):
    """Typed schema for user.json (role and future per-user metadata)."""

    role: UserRole = 'user'

    @field_validator('role')
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f'Invalid role {v!r}. Must be one of: {sorted(_VALID_ROLES)}')
        return v


def _user_dir(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug


def user_exists(slug: str) -> bool:
    """
    Return True if the user directory exists.

    Args:
        slug: The user's short identifier (e.g. ``"shaun"``).

    Returns:
        ``True`` if ``data/users/{slug}/`` exists, ``False`` otherwise.
    """
    return _user_dir(slug).is_dir()


def load_user_profile(slug: str) -> str:
    """
    Load the user's profile markdown.

    Args:
        slug: The user's short identifier.

    Returns:
        Raw markdown content of ``profile.md``, or an empty string if the
        file does not exist.
    """
    path = _user_dir(slug) / 'profile.md'
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def save_user_profile(slug: str, content: str) -> None:
    """
    Persist the user's profile markdown.

    Creates the user directory if it does not already exist.

    Args:
        slug: The user's short identifier.
        content: Raw markdown to write to ``profile.md``.
    """
    path = _user_dir(slug) / 'profile.md'
    atomic_write(path, content)


def _load_user_meta(slug: str) -> UserMeta:
    """Load and validate user.json, returning defaults on error."""
    path = _user_dir(slug) / 'user.json'
    if not path.exists():
        return UserMeta()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        return UserMeta.model_validate(raw)
    except Exception:
        return UserMeta()


def get_user_role(slug: str) -> str:
    """
    Return the user's role: ``'admin'`` or ``'user'`` (default).

    Reads ``users/{slug}/user.json``. If the file is absent or the role
    field is missing/invalid, returns ``'user'``.

    Args:
        slug: The user's short identifier.

    Returns:
        ``'admin'`` or ``'user'``.
    """
    return _load_user_meta(slug).role


def set_user_role(slug: str, role: str) -> None:
    """
    Persist the user's role.

    Creates the user directory if it does not already exist.

    Args:
        slug: The user's short identifier.
        role: Must be ``'admin'`` or ``'user'``.

    Raises:
        ValueError: If ``role`` is not a valid role.
    """
    meta = UserMeta(role=role)  # type: ignore[arg-type]  # validates via field_validator
    path = _user_dir(slug) / 'user.json'
    atomic_write(path, meta.model_dump_json(indent=2))
