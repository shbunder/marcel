"""Flag file helpers for watchdog ↔ agent communication.

Flag files live at ``data/watchdog/`` (relative to repo root):

- ``restart_requested`` — written by the agent to request a restart;
  contains the pre-change git commit SHA.
- ``restart_result`` — written by the watchdog after a restart:
  ``"ok"`` or ``"rolled_back"``.

All writes use an atomic write-to-temp-then-rename pattern so the watchdog
never sees a partially-written file.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

# Optional override used in tests — set via _set_data_dir().
_data_dir_override: pathlib.Path | None = None


def _repo_root() -> pathlib.Path:
    """Walk up from this file until a directory containing ``.git`` is found."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / '.git').exists():
            return parent
    raise RuntimeError('Could not find repo root (no .git directory found in any ancestor)')


def data_dir() -> pathlib.Path:
    """Return the watchdog data directory, creating it if necessary."""
    if _data_dir_override is not None:
        d = _data_dir_override
    else:
        d = _repo_root() / 'data' / 'watchdog'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_data_dir(path: pathlib.Path | None) -> None:
    """Override the data directory.  Pass ``None`` to restore the default.

    Intended for use in tests only.
    """
    global _data_dir_override
    _data_dir_override = path


def _atomic_write(path: pathlib.Path, text: str) -> None:
    """Write *text* to *path* atomically (write temp file, then rename)."""
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w') as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# restart_requested flag
# ---------------------------------------------------------------------------


def request_restart(pre_change_sha: str) -> None:
    """Write the ``restart_requested`` flag containing *pre_change_sha*."""
    _atomic_write(data_dir() / 'restart_requested', pre_change_sha)


def read_restart_request() -> str | None:
    """Return the pre-change SHA if a restart is requested, else ``None``."""
    path = data_dir() / 'restart_requested'
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def clear_restart_request() -> None:
    """Delete the ``restart_requested`` flag file (no-op if absent)."""
    try:
        (data_dir() / 'restart_requested').unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# restart_result flag
# ---------------------------------------------------------------------------


def write_restart_result(result: str) -> None:
    """Write *result* (``"ok"`` or ``"rolled_back"``) to ``restart_result``."""
    _atomic_write(data_dir() / 'restart_result', result)


def read_restart_result() -> str | None:
    """Return the result string if present, else ``None``."""
    path = data_dir() / 'restart_result'
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def clear_restart_result() -> None:
    """Delete the ``restart_result`` flag file (no-op if absent)."""
    try:
        (data_dir() / 'restart_result').unlink()
    except FileNotFoundError:
        pass
