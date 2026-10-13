"""What every part of the mock shares: the clock and the refusal."""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


class Refused(Exception):
    """A request the hub would refuse: carries the status and the `Error` body."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
