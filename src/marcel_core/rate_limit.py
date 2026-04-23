"""Per-key in-memory token-bucket rate limiter.

Used by the WebSocket chat endpoint (ISSUE-022) to cap incoming messages
per user. A single-container family assistant doesn't need Redis-backed
distributed rate limiting; the in-memory dict resets on restart, which is
acceptable for a denial-of-service guard (an attacker who can sustain
abuse across the watchdog rollback has bigger problems).

Algorithm: standard token bucket.

- Each key (e.g. ``user_slug``) owns a ``tokens: float`` + ``last_refill``
  timestamp.
- ``allow(key)`` refills ``elapsed * rate_per_second`` tokens capped at
  ``burst``, then deducts 1 token if available. Returns ``True`` if the
  request is allowed, ``False`` otherwise.
- Wall-clock comes from :func:`time.monotonic` — unaffected by NTP steps
  or manual ``date`` adjustments.

The bucket is a value object; construction is cheap. Callers typically
instantiate a single module-level bucket with settings-driven rate +
burst. Tests should build their own with injected ``time_source`` so
they can advance time deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _BucketState:
    """Per-key mutable state. Module-private; callers see the facade below."""

    tokens: float
    last_refill: float


class TokenBucket:
    """Thread-safeish token-bucket rate limiter keyed by an arbitrary string.

    Not truly thread-safe — Python's GIL plus dict atomicity makes the
    happy path race-free, but a writer-interleaved pathological schedule
    could in principle double-spend. For a single-event-loop FastAPI
    app that doesn't matter; for anything that expects hard correctness
    under thread contention, wrap it in a lock.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError('rate_per_second must be positive')
        if burst <= 0:
            raise ValueError('burst must be positive')
        self._rate = float(rate_per_second)
        self._burst = float(burst)
        self._now = time_source
        self._state: dict[str, _BucketState] = {}

    @property
    def rate_per_second(self) -> float:
        return self._rate

    @property
    def burst(self) -> int:
        return int(self._burst)

    def allow(self, key: str) -> bool:
        """Return True if one token is available for *key*, deducting it."""
        now = self._now()
        state = self._state.get(key)
        if state is None:
            # First request for this key — start with a full bucket.
            state = _BucketState(tokens=self._burst, last_refill=now)
            self._state[key] = state

        elapsed = max(0.0, now - state.last_refill)
        state.tokens = min(self._burst, state.tokens + elapsed * self._rate)
        state.last_refill = now

        if state.tokens >= 1.0:
            state.tokens -= 1.0
            return True
        return False

    def reset(self, key: str | None = None) -> None:
        """Clear state. Intended for tests — production code should not call this."""
        if key is None:
            self._state.clear()
        else:
            self._state.pop(key, None)


_ws_bucket: TokenBucket | None = None


def get_ws_bucket() -> TokenBucket:
    """Return the process-wide WebSocket message rate-limit bucket.

    Lazy-constructed from :mod:`marcel_core.config.settings` on first
    use so test suites that monkeypatch settings observe their values.
    Subsequent calls return the same instance.
    """
    global _ws_bucket
    if _ws_bucket is None:
        from marcel_core.config import settings

        _ws_bucket = TokenBucket(
            rate_per_second=settings.marcel_ws_rate_limit_per_second,
            burst=settings.marcel_ws_rate_limit_burst,
        )
    return _ws_bucket


def _reset_ws_bucket_for_tests() -> None:
    """Force re-construction on next :func:`get_ws_bucket` call.

    Used by tests that need to flip settings and observe a fresh bucket.
    Not part of the public API.
    """
    global _ws_bucket
    _ws_bucket = None
