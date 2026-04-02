"""
Data root directory resolution.

The data root is determined in this order:
1. ``_DATA_ROOT`` module-level override (used in tests).
2. ``MARCEL_DATA_DIR`` environment variable.
3. Default: ``~/.marcel/`` (the standard Marcel data directory).
"""

import os
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
    env = os.environ.get('MARCEL_DATA_DIR')
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / '.marcel'
