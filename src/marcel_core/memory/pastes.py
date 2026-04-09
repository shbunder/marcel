"""External paste store for large tool outputs.

Instead of embedding large content (>1KB) directly in JSONL history, we:
1. Hash the content (SHA-256)
2. Store content in a separate file: ~/.marcel/users/{slug}/.pastes/{hash}
3. Reference it in history with result_ref: "sha256:{hash}"

Benefits:
- Keeps JSONL history compact and fast to scan
- Deduplicates identical large outputs
- Lazy-loaded only when needed for display
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)

# Content larger than this threshold gets offloaded to paste store
PASTE_THRESHOLD = 1024  # 1KB


def _pastes_dir(user_slug: str) -> Path:
    """Return the path to the user's pastes directory."""
    return data_root() / 'users' / user_slug / '.pastes'


def _content_hash(content: str) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def store_paste(user_slug: str, content: str) -> str:
    """Store large content in paste store and return reference.

    Args:
        user_slug: The user's slug.
        content: The content to store.

    Returns:
        Content reference in format "sha256:{hash}".
    """
    if len(content) < PASTE_THRESHOLD:
        # Don't bother storing small content
        return content

    content_hash = _content_hash(content)
    ref = f'sha256:{content_hash}'

    pastes_dir = _pastes_dir(user_slug)
    pastes_dir.mkdir(parents=True, exist_ok=True)

    paste_path = pastes_dir / content_hash
    if not paste_path.exists():
        paste_path.write_text(content, encoding='utf-8')
        log.debug('Stored paste %s (%d bytes)', content_hash[:12], len(content))

    return ref


def retrieve_paste(user_slug: str, ref: str) -> str | None:
    """Retrieve content from paste store.

    Args:
        user_slug: The user's slug.
        ref: Content reference (e.g., "sha256:abc123...").

    Returns:
        The stored content, or None if not found.
    """
    if not ref.startswith('sha256:'):
        # Not a paste reference, return as-is
        return ref

    content_hash = ref.removeprefix('sha256:')
    paste_path = _pastes_dir(user_slug) / content_hash

    if not paste_path.exists():
        log.warning('Paste not found: %s', ref)
        return None

    return paste_path.read_text(encoding='utf-8')


def should_store_as_paste(content: str) -> bool:
    """Check if content should be stored in paste store.

    Args:
        content: The content to check.

    Returns:
        True if content exceeds threshold and should be stored as paste.
    """
    return len(content) >= PASTE_THRESHOLD
