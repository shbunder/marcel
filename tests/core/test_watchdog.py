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
def isolated_data_dir(tmp_path: pathlib.Path):
    """Redirect flag file I/O to a temporary directory for every test."""
    flags._set_data_dir(tmp_path)
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
    assert not (tmp_path / 'restart_requested').exists()


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
    assert not (tmp_path / 'restart_result').exists()


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
