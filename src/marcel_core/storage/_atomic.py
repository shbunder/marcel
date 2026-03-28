"""Atomic file write helper: write to temp file, then os.rename."""

import os
import pathlib
import tempfile


def atomic_write(path: pathlib.Path, content: str) -> None:
    """
    Write content to path atomically using a temp file + rename.

    Creates parent directories as needed. Guarantees that either the full
    content is written or the original file is left untouched — a partial
    write will never be visible to readers.

    Args:
        path: Destination file path.
        content: Text content to write (UTF-8).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
