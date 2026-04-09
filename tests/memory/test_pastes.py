"""Tests for paste store module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from marcel_core.memory.pastes import PASTE_THRESHOLD, retrieve_paste, should_store_as_paste, store_paste


@pytest.fixture
def temp_data_root(tmp_path: Path):
    """Patch data_root to use temporary directory."""
    with patch('marcel_core.memory.pastes.data_root', return_value=tmp_path):
        yield tmp_path


def test_small_content_not_stored(temp_data_root: Path):
    """Test that small content is returned as-is, not stored."""
    small_content = 'a' * 100  # 100 bytes, below threshold
    result = store_paste('test_user', small_content)

    assert result == small_content  # Not a reference
    pastes_dir = temp_data_root / 'users' / 'test_user' / '.pastes'
    assert not pastes_dir.exists() or not list(pastes_dir.iterdir())


def test_large_content_stored_as_paste(temp_data_root: Path):
    """Test that large content is stored and referenced."""
    large_content = 'a' * (PASTE_THRESHOLD + 100)
    ref = store_paste('test_user', large_content)

    assert ref.startswith('sha256:')
    assert ref != large_content

    # Check paste file exists
    pastes_dir = temp_data_root / 'users' / 'test_user' / '.pastes'
    assert pastes_dir.exists()
    paste_files = list(pastes_dir.iterdir())
    assert len(paste_files) == 1


def test_retrieve_paste(temp_data_root: Path):
    """Test retrieving content from paste store."""
    large_content = 'Large content ' * 100
    ref = store_paste('test_user', large_content)

    retrieved = retrieve_paste('test_user', ref)
    assert retrieved == large_content


def test_retrieve_nonexistent_paste(temp_data_root: Path):
    """Test retrieving non-existent paste returns None."""
    ref = 'sha256:nonexistent_hash'
    result = retrieve_paste('test_user', ref)
    assert result is None


def test_retrieve_non_paste_reference():
    """Test that non-paste strings are returned as-is."""
    small_text = 'Just some text'
    result = retrieve_paste('test_user', small_text)
    assert result == small_text


def test_deduplication(temp_data_root: Path):
    """Test that identical content reuses the same paste."""
    content = 'Repeated content ' * 100

    ref1 = store_paste('test_user', content)
    ref2 = store_paste('test_user', content)

    assert ref1 == ref2  # Same hash

    # Only one paste file
    pastes_dir = temp_data_root / 'users' / 'test_user' / '.pastes'
    paste_files = list(pastes_dir.iterdir())
    assert len(paste_files) == 1


def test_should_store_as_paste():
    """Test threshold check for paste storage."""
    small = 'a' * (PASTE_THRESHOLD - 1)
    assert not should_store_as_paste(small)

    large = 'a' * PASTE_THRESHOLD
    assert should_store_as_paste(large)

    very_large = 'a' * (PASTE_THRESHOLD * 10)
    assert should_store_as_paste(very_large)
