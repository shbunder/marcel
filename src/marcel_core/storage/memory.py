"""Memory storage: load, save, and index topic-scoped memory files."""

import pathlib

from ._atomic import atomic_write
from ._root import data_root


def _memory_dir(slug: str) -> pathlib.Path:
    return data_root() / 'users' / slug / 'memory'


def _memory_path(slug: str, topic: str) -> pathlib.Path:
    """
    Return the path for a memory topic file.

    ``topic`` is the filename without ``.md``, e.g. ``"calendar"``.
    """
    filename = topic if topic.endswith('.md') else f'{topic}.md'
    return _memory_dir(slug) / filename


def _index_path(slug: str) -> pathlib.Path:
    return _memory_dir(slug) / 'index.md'


def load_memory_index(slug: str) -> str:
    """
    Return the raw markdown content of the memory index.

    Args:
        slug: The user's short identifier.

    Returns:
        Raw markdown of ``memory/index.md``, or an empty string if it does
        not exist.
    """
    path = _index_path(slug)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def load_memory_file(slug: str, topic: str) -> str:
    """
    Return the raw markdown content of a memory topic file.

    Args:
        slug: The user's short identifier.
        topic: Topic filename stem (e.g. ``"calendar"``).

    Returns:
        Raw markdown, or an empty string if the file does not exist.
    """
    path = _memory_path(slug, topic)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


def save_memory_file(slug: str, topic: str, content: str) -> None:
    """
    Persist a memory topic file.

    Creates the memory directory if it does not already exist.

    Args:
        slug: The user's short identifier.
        topic: Topic filename stem (e.g. ``"calendar"``).
        content: Raw markdown to write.
    """
    path = _memory_path(slug, topic)
    atomic_write(path, content)


def update_memory_index(slug: str, topic: str, description: str) -> None:
    """
    Add a topic entry to the memory index if not already present.

    Creates the index file if it does not already exist.  Does nothing if an
    entry for ``topic`` already appears in the index.

    Args:
        slug: The user's short identifier.
        topic: Topic filename stem (e.g. ``"calendar"``).
        description: A short human-readable summary of the memory file.
    """
    path = _index_path(slug)
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    filename = topic if topic.endswith('.md') else f'{topic}.md'
    # Don't add a duplicate entry — check for the link anchor `[filename]`.
    if f'[{filename}]' in existing:
        return
    entry = f'- [{filename}]({filename}) — {description}\n'
    updated = existing + entry
    atomic_write(path, updated)
