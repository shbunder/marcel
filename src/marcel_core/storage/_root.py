"""
Data root directory resolution.

The data root is determined in this order:
1. ``_DATA_ROOT`` module-level override (used in tests).
2. ``settings.data_dir`` (reads ``MARCEL_DATA_DIR`` env var or defaults to ``~/.marcel/``).
"""

import pathlib

# Override point for tests — set this to a ``pathlib.Path`` before importing
# any other storage module, or patch it directly in test fixtures.
_DATA_ROOT: pathlib.Path | None = None


def data_root() -> pathlib.Path:
    """
    Return the resolved data root directory.

    Returns:
        An absolute ``pathlib.Path`` pointing at the data directory.
    """
    if _DATA_ROOT is not None:
        return _DATA_ROOT
    from marcel_core.config import settings

    return settings.data_dir
