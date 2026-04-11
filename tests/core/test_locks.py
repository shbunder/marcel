"""Tests for storage/_locks.py — per-user asyncio.Lock registry."""

from marcel_core.storage._locks import get_lock


class TestGetLock:
    def test_returns_lock(self):
        lock = get_lock('alice')
        import asyncio

        assert isinstance(lock, asyncio.Lock)

    def test_same_slug_same_lock(self):
        lock1 = get_lock('alice')
        lock2 = get_lock('alice')
        assert lock1 is lock2

    def test_different_slug_different_lock(self):
        lock_a = get_lock('alice_lock_test')
        lock_b = get_lock('bob_lock_test')
        assert lock_a is not lock_b
