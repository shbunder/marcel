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
        await store.read('alice/sha256:' + 'deadbeef' * 8)


async def test_malformed_handle_rejected(store, as_alice):
    with pytest.raises(PermissionError):
        await store.read('no-separator-here')


async def test_traversal_handle_rejected(store, as_alice, tmp_path):
    """Regression: a ref with path characters must never reach the disk.

    Pre-fix, 'alice/sha256:../../bob/credentials.enc' passed the owner
    check (owner == alice) and retrieve_paste joined the ref into the
    path — reading any file reachable from alice's .pastes/ via '..'
    (found by pre-close-verifier on FEAT-260718-ed6d63).
    """
    victim = tmp_path / 'users' / 'bob' / 'credentials.enc'
    victim.parent.mkdir(parents=True)
    victim.write_text('bob-secret')

    for evil in (
        'alice/sha256:../../bob/credentials.enc',
        'alice/sha256:' + 'a' * 63,  # wrong length
        'alice/sha256:' + 'A' * 64,  # wrong case — not our hash alphabet
        'alice/../bob/sha256:' + 'a' * 64,  # owner segment itself poisoned
    ):
        with pytest.raises(PermissionError):
            await store.read(evil)


async def test_retrieve_paste_rejects_traversal_refs(tmp_path, monkeypatch):
    """Defense-in-depth: the paste layer refuses non-hash refs on its own."""
    from marcel_core.memory.pastes import retrieve_paste

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    victim = tmp_path / 'users' / 'bob' / 'credentials.enc'
    victim.parent.mkdir(parents=True)
    victim.write_text('bob-secret')

    assert retrieve_paste('alice', 'sha256:../../bob/credentials.enc') is None
