"""Unit tests for PasteOverflowStore — ownership scoping and fallbacks."""

from __future__ import annotations

import pytest

from marcel_core.capabilities.persistence.overflow import (
    PasteOverflowStore,
    current_overflow_user,
)
from marcel_core.storage import _root


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    return PasteOverflowStore()


@pytest.fixture
def as_alice():
    token = current_overflow_user.set('alice')
    yield
    current_overflow_user.reset(token)


async def test_write_read_round_trip(store, as_alice):
    payload = b'the full payload ' * 100  # above PASTE_THRESHOLD (1KB)
    handle = await store.write('key-1', payload)
    assert handle.startswith('alice/sha256:')
    assert await store.read(handle) == payload


async def test_sub_threshold_payload_falls_back(store, as_alice):
    """A spill band configured below PASTE_THRESHOLD must not mint broken handles."""
    with pytest.raises(RuntimeError, match='falling back'):
        await store.write('key-1', b'tiny')


async def test_no_turn_user_write_raises_for_band_fallback(store):
    with pytest.raises(RuntimeError, match='falling back'):
        await store.write('key-1', b'payload')


async def test_cross_user_handle_rejected(store, as_alice):
    handle = await store.write('key-1', b'secret payload ' * 100)
    token = current_overflow_user.set('bob')
    try:
        with pytest.raises(PermissionError):
            await store.read(handle)
    finally:
        current_overflow_user.reset(token)


async def test_missing_paste_raises_key_error(store, as_alice):
    with pytest.raises(KeyError, match='cleaned up'):
        await store.read('alice/sha256:deadbeef')


async def test_malformed_handle_rejected(store, as_alice):
    with pytest.raises(PermissionError):
        await store.read('no-separator-here')
