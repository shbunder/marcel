"""
Data root directory resolution.

The data root is determined in this order:
1. ``_DATA_ROOT`` module-level override (used in tests).
2. ``MARCEL_DATA_DIR`` environment variable.
3. Default: ``{repo_root}/data`` where ``repo_root`` is located by walking
   up from this file until a ``.git`` directory is found.
"""

import os
import pathlib

# Override point for tests — set this to a ``pathlib.Path`` before importing
# any other storage module, or patch it directly in test fixtures.
_DATA_ROOT: pathlib.Path | None = None


def _find_repo_root() -> pathlib.Path:
    """Walk up from this file to find the directory containing ``.git``."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / '.git').exists():
            return parent
    # Fallback: assume this file lives at src/marcel_core/storage/_root.py
    # so the repo root is five levels up.
    return here.parent.parent.parent.parent.parent


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
    return _find_repo_root() / 'data'
