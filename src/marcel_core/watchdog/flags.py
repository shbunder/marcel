"""Flag file helpers for watchdog ↔ agent communication.

Flag files live at ``~/.marcel/watchdog/`` (or ``MARCEL_DATA_DIR/watchdog/``)
with an env suffix so the dev and prod containers cannot race each other's
restart cycles:

- ``restart_requested.<env>`` — written by the agent to request a restart;
  contains the pre-change git commit SHA.
- ``restart_result.<env>`` — written by the watchdog after a restart:
  ``"ok"`` or ``"rolled_back"``.

``<env>`` is resolved from ``MARCEL_ENV`` at call time (default ``prod``).

All writes use an atomic write-to-temp-then-rename pattern so the watchdog
never sees a partially-written file.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

# Optional override used in tests — set via _set_data_dir().
_data_dir_override: pathlib.Path | None = None


def _marcel_data_root() -> pathlib.Path:
    """Return the Marcel data root (from env or default ``~/.marcel/``)."""
    env = os.environ.get('MARCEL_DATA_DIR')
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / '.marcel'


def data_dir() -> pathlib.Path:
    """Return the watchdog data directory, creating it if necessary."""
    if _data_dir_override is not None:
        d = _data_dir_override
    else:
        d = _marcel_data_root() / 'watchdog'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_data_dir(path: pathlib.Path | None) -> None:
    """Override the data directory.  Pass ``None`` to restore the default.

    Intended for use in tests only.
    """
    global _data_dir_override
    _data_dir_override = path


# Why not ``settings.marcel_env``? MARCEL_ENV is intentionally not a Settings
# field — this function is the single source of truth. Three reasons:
#   1. Call-time semantics. ``settings`` is a module-level singleton bound once
#      at import. Tests override ``MARCEL_ENV`` per-test with monkeypatch and
#      expect the next flag-file read/write to reflect the new value.
#   2. Safety default on garbage input. We fall back to ``'prod'`` on any value
#      outside ``{dev, prod}`` — a dev flag cannot accidentally trigger the
#      prod rebuild path. pydantic-settings would raise ``ValidationError`` at
#      boot on the same input, preventing the process from starting at all.
#   3. No import cycle. ``watchdog/flags.py`` sits below ``config.py`` in the
#      dep graph; reading ``os.environ`` keeps it that way.
def _env() -> str:
    """Resolve ``MARCEL_ENV`` at call time, defaulting to ``prod``."""
    val = os.environ.get('MARCEL_ENV', 'prod')
    return val if val in ('dev', 'prod') else 'prod'


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


def _request_path() -> pathlib.Path:
    return data_dir() / f'restart_requested.{_env()}'


def request_restart(pre_change_sha: str) -> None:
    """Write the env-scoped ``restart_requested`` flag containing *pre_change_sha*."""
    _atomic_write(_request_path(), pre_change_sha)


def read_restart_request() -> str | None:
    """Return the pre-change SHA if a restart is requested, else ``None``."""
    try:
        return _request_path().read_text().strip() or None
    except FileNotFoundError:
        return None


def clear_restart_request() -> None:
    """Delete the env-scoped ``restart_requested`` flag file (no-op if absent)."""
    try:
        _request_path().unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# restart_result flag
# ---------------------------------------------------------------------------


def _result_path() -> pathlib.Path:
    return data_dir() / f'restart_result.{_env()}'


def write_restart_result(result: str) -> None:
    """Write *result* (``"ok"`` or ``"rolled_back"``) to the env-scoped flag."""
    _atomic_write(_result_path(), result)


def read_restart_result() -> str | None:
    """Return the result string if present, else ``None``."""
    try:
        return _result_path().read_text().strip() or None
    except FileNotFoundError:
        return None


def clear_restart_result() -> None:
    """Delete the env-scoped ``restart_result`` flag file (no-op if absent)."""
    try:
        _result_path().unlink()
    except FileNotFoundError:
        pass
