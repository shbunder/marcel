"""Tests for AI-driven memory selector."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.memory.selector import MAX_SELECTED, _parse_selection, select_relevant_memories
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


@pytest.mark.asyncio
async def test_empty_content_skipped(mock_headers):
    """Files with empty content are skipped from results."""
    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=mock_headers),
        patch('marcel_core.memory.selector.load_memory_file', return_value='   '),  # whitespace only
    ):
        results = await select_relevant_memories('test_user', 'query', include_household=False)
        assert results == []


@pytest.mark.asyncio
async def test_no_freshness_note_when_fresh(mock_headers):
    """Fresh memories (mtime = now) do not get a freshness note appended."""
    import time

    fresh_header = MemoryHeader(
        filename='fresh.md',
        filepath=Path('/data/users/test_user/memory/fresh.md'),
        type=MemoryType.PREFERENCE,
        description='Fresh memory',
        mtime=time.time(),
    )
    with (
        patch('marcel_core.memory.selector.scan_memory_headers', return_value=[fresh_header]),
        patch('marcel_core.memory.selector.load_memory_file', return_value='Fresh content'),
    ):
        results = await select_relevant_memories('test_user', 'query', include_household=False)
        assert len(results) == 1
        _, content = results[0]
        assert '### [preference] fresh (today)' in content
        assert 'Fresh content' in content
        assert '⚠' not in content  # no freshness warning appended


# ---------------------------------------------------------------------------
# _parse_selection (memory/selector.py version)
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_plain_json_array(self):
        assert _parse_selection('["a.md", "b.md"]') == ['a.md', 'b.md']

    def test_empty_array(self):
        assert _parse_selection('[]') == []

    def test_code_fence_with_closing(self):
        assert _parse_selection('```json\n["foo.md"]\n```') == ['foo.md']

    def test_code_fence_without_closing(self):
        # Last line isn't ``` so lines[1:] is used
        assert _parse_selection('```\n["bar.md"]') == ['bar.md']

    def test_invalid_json_returns_empty(self):
        assert _parse_selection('not valid json') == []

    def test_non_array_returns_empty(self):
        assert _parse_selection('{"key": "value"}') == []
