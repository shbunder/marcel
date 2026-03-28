"""User profile storage: existence check, load, and save."""

import pathlib

from ._atomic import atomic_write
from ._root import data_root


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
