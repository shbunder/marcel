"""Tests for ISSUE-022 — the WebSocket rate-limit token bucket.

Tests use an injected time source so the refill/burst behaviour is
deterministic — no real `asyncio.sleep`, no wall-clock dependency.
"""

from __future__ import annotations

import pytest

from marcel_core.rate_limit import (
    TokenBucket,
    _reset_ws_bucket_for_tests,
    get_ws_bucket,
)


class _FakeClock:
    """Monotonic clock substitute that advances only when ``tick`` is called."""

    def __init__(self, start: float = 100.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def tick(self, seconds: float) -> None:
        self._now += seconds


class TestConstructor:
    def test_rejects_nonpositive_rate(self):
        with pytest.raises(ValueError, match='rate_per_second'):
            TokenBucket(rate_per_second=0, burst=5)

    def test_rejects_nonpositive_burst(self):
        with pytest.raises(ValueError, match='burst'):
            TokenBucket(rate_per_second=5, burst=0)

    def test_exposes_config(self):
        b = TokenBucket(rate_per_second=7.5, burst=12)
        assert b.rate_per_second == 7.5
        assert b.burst == 12


class TestAllow:
    def test_first_request_always_allowed(self):
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=3, time_source=clock)
        assert b.allow('alice') is True

    def test_burst_exhausts_then_rejects(self):
        """burst=3 → first three requests pass, the fourth fails."""
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=3, time_source=clock)
        # No time advance — refill contributes zero tokens.
        assert b.allow('alice') is True  # 3 → 2
        assert b.allow('alice') is True  # 2 → 1
        assert b.allow('alice') is True  # 1 → 0
        assert b.allow('alice') is False  # 0 → rejected

    def test_refill_respects_rate_per_second(self):
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=2.0, burst=3, time_source=clock)
        # Drain the bucket.
        for _ in range(3):
            b.allow('alice')
        assert b.allow('alice') is False

        # Half a second @ 2 tokens/sec = 1 token refilled.
        clock.tick(0.5)
        assert b.allow('alice') is True
        assert b.allow('alice') is False  # drained again

    def test_refill_caps_at_burst(self):
        """A long idle must not let tokens accumulate past ``burst``."""
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=2, time_source=clock)
        # Long idle — would refill 100 tokens uncapped.
        clock.tick(100)
        # Cap means only 2 tokens exist: two passes, third fails.
        assert b.allow('alice') is True
        assert b.allow('alice') is True
        assert b.allow('alice') is False

    def test_keys_are_isolated(self):
        """Alice exhausting her bucket does not affect Bob."""
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=2, time_source=clock)
        assert b.allow('alice') is True
        assert b.allow('alice') is True
        assert b.allow('alice') is False
        # Bob starts with a full bucket.
        assert b.allow('bob') is True
        assert b.allow('bob') is True
        assert b.allow('bob') is False

    def test_reset_clears_all_keys(self):
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=2, time_source=clock)
        b.allow('alice')
        b.allow('alice')
        assert b.allow('alice') is False
        b.reset()
        # Fresh — back to full burst.
        assert b.allow('alice') is True
        assert b.allow('alice') is True
        assert b.allow('alice') is False

    def test_reset_single_key(self):
        clock = _FakeClock()
        b = TokenBucket(rate_per_second=1, burst=1, time_source=clock)
        b.allow('alice')  # exhausts
        b.allow('bob')  # exhausts
        b.reset('alice')
        assert b.allow('alice') is True  # refreshed
        assert b.allow('bob') is False  # still exhausted

    def test_non_monotonic_clock_does_not_refund_tokens(self):
        """If the injected clock moves backward, refill is zero (not negative)."""
        clock = _FakeClock(start=100.0)
        b = TokenBucket(rate_per_second=1, burst=2, time_source=clock)
        b.allow('alice')
        b.allow('alice')
        assert b.allow('alice') is False
        # Clock jumps backward — elapsed is clamped to 0, no refill.
        clock._now = 50.0
        assert b.allow('alice') is False


# ---------------------------------------------------------------------------
# Module-level WS bucket — settings integration
# ---------------------------------------------------------------------------


class TestGetWsBucket:
    def test_is_singleton(self):
        _reset_ws_bucket_for_tests()
        first = get_ws_bucket()
        second = get_ws_bucket()
        assert first is second
        _reset_ws_bucket_for_tests()

    def test_reads_settings_on_first_use(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_per_second', 2.5)
        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_burst', 7)

        _reset_ws_bucket_for_tests()
        b = get_ws_bucket()
        assert b.rate_per_second == 2.5
        assert b.burst == 7
        _reset_ws_bucket_for_tests()

    def test_reset_for_tests_forces_reconstruction(self, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_per_second', 1.0)
        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_burst', 1)
        _reset_ws_bucket_for_tests()
        first = get_ws_bucket()

        # Flip settings, reset, expect new values observed.
        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_per_second', 10.0)
        monkeypatch.setattr(settings, 'marcel_ws_rate_limit_burst', 20)
        _reset_ws_bucket_for_tests()
        second = get_ws_bucket()

        assert first is not second
        assert second.rate_per_second == 10.0
        assert second.burst == 20
        _reset_ws_bucket_for_tests()
