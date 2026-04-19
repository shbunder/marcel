"""Tests for ISSUE-005: watchdog + git rollback."""

from __future__ import annotations

import pathlib
import subprocess
from http.client import HTTPResponse
from unittest.mock import MagicMock, patch

import pytest

from marcel_core.watchdog import flags, health, rollback

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: pathlib.Path, monkeypatch):
    """Redirect flag file I/O to a temporary directory for every test.

    Pin ``MARCEL_ENV=prod`` so the base flag-file tests exercise a deterministic
    suffix; env-specific behavior is covered by the dedicated tests below.
    """
    flags._set_data_dir(tmp_path)
    monkeypatch.setenv('MARCEL_ENV', 'prod')
    yield tmp_path
    flags._set_data_dir(None)


# ---------------------------------------------------------------------------
# flags.py — restart_requested
# ---------------------------------------------------------------------------


def test_request_restart_round_trip():
    sha = 'abc1234'
    flags.request_restart(sha)
    assert flags.read_restart_request() == sha


def test_read_restart_request_absent_returns_none():
    assert flags.read_restart_request() is None


def test_clear_restart_request_removes_file(tmp_path: pathlib.Path):
    flags.request_restart('deadbeef')
    flags.clear_restart_request()
    assert flags.read_restart_request() is None
    assert not (tmp_path / 'restart_requested.prod').exists()


def test_clear_restart_request_noop_when_absent():
    # Should not raise even when the file doesn't exist.
    flags.clear_restart_request()


# ---------------------------------------------------------------------------
# flags.py — restart_result
# ---------------------------------------------------------------------------


def test_write_restart_result_round_trip():
    flags.write_restart_result('ok')
    assert flags.read_restart_result() == 'ok'


def test_write_restart_result_rolled_back():
    flags.write_restart_result('rolled_back')
    assert flags.read_restart_result() == 'rolled_back'


def test_read_restart_result_absent_returns_none():
    assert flags.read_restart_result() is None


def test_clear_restart_result_removes_file(tmp_path: pathlib.Path):
    flags.write_restart_result('ok')
    flags.clear_restart_result()
    assert flags.read_restart_result() is None
    assert not (tmp_path / 'restart_result.prod').exists()


def test_clear_restart_result_noop_when_absent():
    # Should not raise even when the file doesn't exist.
    flags.clear_restart_result()


# ---------------------------------------------------------------------------
# health.py — poll_health
# ---------------------------------------------------------------------------


def _make_fake_response(status: int = 200) -> HTTPResponse:
    """Return a minimal mock that looks like a urllib HTTP response."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_poll_health_returns_true_on_200():
    fake_resp = _make_fake_response(200)
    with patch('urllib.request.urlopen', return_value=fake_resp):
        result = health.poll_health(port=8000, timeout_s=5.0, interval_s=0.1)
    assert result is True


def test_poll_health_returns_false_on_timeout():
    """Simulate a server that never responds — poll_health must time out."""
    with patch('urllib.request.urlopen', side_effect=OSError('connection refused')):
        # Use a very short timeout so the test doesn't block.
        result = health.poll_health(port=8000, timeout_s=0.3, interval_s=0.1)
    assert result is False


def test_poll_health_returns_false_on_non_200():
    """A 500 response should not satisfy the health check."""
    fake_resp = _make_fake_response(500)
    with patch('urllib.request.urlopen', return_value=fake_resp):
        result = health.poll_health(port=8000, timeout_s=0.3, interval_s=0.1)
    assert result is False


# ---------------------------------------------------------------------------
# rollback.py — do_rollback
# ---------------------------------------------------------------------------


def test_do_rollback_calls_git_revert(tmp_path: pathlib.Path):
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        rollback.do_rollback(tmp_path)

    mock_run.assert_called_once_with(
        ['git', 'revert', 'HEAD', '--no-edit'],
        cwd=tmp_path,
        check=True,
    )


def test_do_rollback_propagates_error(tmp_path: pathlib.Path):
    with patch(
        'subprocess.run',
        side_effect=subprocess.CalledProcessError(1, 'git revert'),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            rollback.do_rollback(tmp_path)


# ---------------------------------------------------------------------------
# flags.py — env-var-based data dir
# ---------------------------------------------------------------------------


def test_data_dir_uses_env_var(tmp_path: pathlib.Path, monkeypatch):
    """When MARCEL_DATA_DIR is set, watchdog writes to <DATA_DIR>/watchdog/."""
    flags._set_data_dir(None)  # disable override
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))

    d = flags.data_dir()
    assert str(tmp_path) in str(d)
    assert d.exists()

    # Restore override so other tests stay isolated
    flags._set_data_dir(tmp_path)


def test_data_dir_fallback_to_home(tmp_path: pathlib.Path, monkeypatch):
    """Without MARCEL_DATA_DIR, data_dir uses ~/.marcel/watchdog."""
    flags._set_data_dir(None)
    monkeypatch.delenv('MARCEL_DATA_DIR', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))

    d = flags.data_dir()
    # Should be inside HOME
    assert str(tmp_path) in str(d)

    flags._set_data_dir(tmp_path)


def test_atomic_write_error_is_reraised(tmp_path: pathlib.Path, monkeypatch):
    """If atomic_write fails midway, temp file is cleaned up and error re-raised."""
    flags._set_data_dir(tmp_path)

    import os

    def broken_replace(src, dst):
        raise OSError('simulated rename failure')

    monkeypatch.setattr(os, 'replace', broken_replace)

    with pytest.raises(OSError, match='simulated rename failure'):
        flags._atomic_write(tmp_path / 'test.txt', 'content')


def test_atomic_write_unlink_fails_during_exception(tmp_path: pathlib.Path, monkeypatch):
    """If unlink also fails during exception cleanup, original error still propagates."""
    flags._set_data_dir(tmp_path)

    import os

    monkeypatch.setattr(os, 'replace', lambda src, dst: (_ for _ in ()).throw(OSError('replace failed')))
    monkeypatch.setattr(os, 'unlink', lambda path: (_ for _ in ()).throw(OSError('unlink failed')))

    with pytest.raises(OSError, match='replace failed'):
        flags._atomic_write(tmp_path / 'test.txt', 'content')


# ---------------------------------------------------------------------------
# flags.py — env-aware flag file names (ISSUE-6b02d0)
# ---------------------------------------------------------------------------


def test_request_restart_writes_env_suffixed_file_dev(tmp_path: pathlib.Path, monkeypatch):
    """MARCEL_ENV=dev must write to restart_requested.dev, not .prod."""
    monkeypatch.setenv('MARCEL_ENV', 'dev')

    flags.request_restart('abc1234')

    assert (tmp_path / 'restart_requested.dev').exists()
    assert not (tmp_path / 'restart_requested.prod').exists()
    assert not (tmp_path / 'restart_requested').exists()


def test_request_restart_writes_env_suffixed_file_prod(tmp_path: pathlib.Path, monkeypatch):
    """MARCEL_ENV=prod must write to restart_requested.prod, not .dev."""
    monkeypatch.setenv('MARCEL_ENV', 'prod')

    flags.request_restart('abc1234')

    assert (tmp_path / 'restart_requested.prod').exists()
    assert not (tmp_path / 'restart_requested.dev').exists()


def test_dev_and_prod_flags_are_isolated(tmp_path: pathlib.Path, monkeypatch):
    """A dev request must not be visible to prod readers (and vice versa).

    This is the core parity property: self-mod in one env cannot trigger the
    other env's restart path.
    """
    monkeypatch.setenv('MARCEL_ENV', 'dev')
    flags.request_restart('dev-sha')

    monkeypatch.setenv('MARCEL_ENV', 'prod')
    assert flags.read_restart_request() is None  # prod reader sees no request

    monkeypatch.setenv('MARCEL_ENV', 'dev')
    assert flags.read_restart_request() == 'dev-sha'


def test_unknown_env_value_falls_back_to_prod(tmp_path: pathlib.Path, monkeypatch):
    """A typoed MARCEL_ENV must fall back to prod (fail-safe default).

    A dev flag file cannot accidentally trigger a prod rebuild because an
    unrecognized value never matches the dev suffix.
    """
    monkeypatch.setenv('MARCEL_ENV', 'staging')  # not in {dev, prod}

    flags.request_restart('abc')

    assert (tmp_path / 'restart_requested.prod').exists()


def test_write_restart_result_is_env_scoped(tmp_path: pathlib.Path, monkeypatch):
    """restart_result is also env-scoped so dev/prod results cannot collide."""
    monkeypatch.setenv('MARCEL_ENV', 'dev')
    flags.write_restart_result('ok')

    assert (tmp_path / 'restart_result.dev').exists()
    assert not (tmp_path / 'restart_result.prod').exists()

    monkeypatch.setenv('MARCEL_ENV', 'prod')
    assert flags.read_restart_result() is None  # prod reader sees no result
