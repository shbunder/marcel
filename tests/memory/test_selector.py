"""Tests for AI-driven memory selector."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.memory.selector import MAX_SELECTED, select_relevant_memories
from marcel_core.storage.memory import MemoryHeader, MemoryType


@pytest.fixture
def mock_headers():
    """Create mock memory headers."""
    return [
        MemoryHeader(
            filename='groceries.md',
            filepath=Path('/data/users/test_user/memory/groceries.md'),
            type=MemoryType.PREFERENCE,
            description='Favorite grocery items',
            mtime=1000.0,
        ),
        MemoryHeader(
            filename='dentist.md',
            filepath=Path('/data/users/test_user/memory/dentist.md'),
            type=MemoryType.SCHEDULE,
            description='Dental appointment schedule',
            mtime=1001.0,
        ),
        MemoryHeader(
            filename='alice.md',
            filepath=Path('/data/users/test_user/memory/alice.md'),
            type=MemoryType.PERSON,
            description='Alice - friend from college',
            mtime=1002.0,
        ),
    ]


@pytest.mark.asyncio
async def test_small_memory_set_loads_all(mock_headers):
    """Test that small memory sets skip AI selection and load everything."""
    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=mock_headers),
        patch('marcel_core.memory.selector.load_memory_file', return_value='Memory content'),
    ):
        results = await select_relevant_memories('test_user', 'what groceries do I like?', include_household=False)

        # Should load all 3 headers (below SELECTION_THRESHOLD)
        assert len(results) == 3


@pytest.mark.asyncio
async def test_large_memory_set_uses_ai_selection(mock_headers):
    """Test that large memory sets use AI selection."""
    # Create enough headers to trigger selection
    many_headers = mock_headers * 5  # 15 headers > SELECTION_THRESHOLD

    mock_agent_result = AsyncMock()
    mock_agent_result.data = '["groceries.md"]'

    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=many_headers),
        patch('marcel_core.memory.selector.Agent') as mock_agent_class,
        patch('marcel_core.memory.selector.load_memory_file', return_value='Memory content'),
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.return_value = mock_agent_result
        mock_agent_class.return_value = mock_agent_instance

        results = await select_relevant_memories('test_user', 'what groceries do I like?', include_household=False)

        # AI should have selected just 1 file
        assert len(results) <= MAX_SELECTED
        mock_agent_instance.run.assert_called_once()


@pytest.mark.asyncio
async def test_ai_selection_failure_fallback(mock_headers):
    """Test that AI selection failures fall back to loading all."""
    many_headers = mock_headers * 5

    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=many_headers),
        patch('marcel_core.memory.selector.Agent') as mock_agent_class,
        patch('marcel_core.memory.selector.load_memory_file', return_value='Memory content'),
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.run.side_effect = Exception('AI failed')
        mock_agent_class.return_value = mock_agent_instance

        results = await select_relevant_memories('test_user', 'what groceries do I like?', include_household=False)

        # Should fall back to first MAX_SELECTED
        assert len(results) == MAX_SELECTED


@pytest.mark.asyncio
async def test_empty_memory_set():
    """Test handling of empty memory set."""
    with patch('marcel_core.memory.selector.scan_memory_headers', return_value=[]):
        results = await select_relevant_memories('test_user', 'any query')
        assert results == []


@pytest.mark.asyncio
async def test_include_household_memories(mock_headers):
    """Test that household memories are included when requested."""
    household_header = MemoryHeader(
        filename='family_schedule.md',
        filepath=Path('/data/users/_household/memory/family_schedule.md'),
        type=MemoryType.SCHEDULE,
        description='Family events',
        mtime=1003.0,
    )

    def mock_scan(user_slug):
        if user_slug == '_household':
            return [household_header]
        return mock_headers

    with (
        patch('marcel_core.memory.selector.scan_memory_headers', side_effect=mock_scan),
        patch('marcel_core.memory.selector.load_memory_file', return_value='Memory content'),
    ):
        results = await select_relevant_memories('test_user', 'family events?', include_household=True)

        # Should have both user and household memories
        assert len(results) == 4  # 3 user + 1 household


@pytest.mark.asyncio
async def test_freshness_note_appended(mock_headers):
    """Test that freshness notes are appended to old memories."""
    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=mock_headers),
        patch('marcel_core.memory.selector.load_memory_file', return_value='Memory content'),
        patch('marcel_core.memory.selector.memory_freshness_note', return_value='⚠️ Memory is 2 days old'),
    ):
        results = await select_relevant_memories('test_user', 'query')

        # Check that freshness note was appended
        for _, content in results:
            assert '⚠️ Memory is 2 days old' in content
