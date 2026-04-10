"""Tests for memory/selector.py — AI-driven memory selection.

Note: agent/memory_select.py re-exports from memory/selector.py.
These tests target the canonical implementation directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.memory.selector import (
    MAX_SELECTED,
    SELECTION_THRESHOLD,
    _parse_selection,
    _select_via_model,
    select_relevant_memories,
)
from marcel_core.storage.memory import MemoryHeader, MemoryType


def _make_header(slug: str, filename: str, mtime: float = 1000.0) -> MemoryHeader:
    return MemoryHeader(
        filename=filename,
        filepath=Path(f'/data/users/{slug}/memory/{filename}'),
        type=MemoryType.PREFERENCE,
        description=f'Description for {filename}',
        mtime=mtime,
    )


# ---------------------------------------------------------------------------
# _parse_selection
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_plain_json_array(self):
        result = _parse_selection('["a.md", "b.md"]')
        assert result == ['a.md', 'b.md']

    def test_empty_array(self):
        result = _parse_selection('[]')
        assert result == []

    def test_json_with_code_fence(self):
        response = '```json\n["foo.md"]\n```'
        result = _parse_selection(response)
        assert result == ['foo.md']

    def test_json_with_bare_code_fence(self):
        response = '```\n["bar.md"]\n```'
        result = _parse_selection(response)
        assert result == ['bar.md']

    def test_invalid_json_returns_empty(self):
        result = _parse_selection('not valid json at all')
        assert result == []

    def test_non_array_json_returns_empty(self):
        result = _parse_selection('{"key": "value"}')
        assert result == []

    def test_filters_non_string_elements(self):
        result = _parse_selection('["a.md", 42, "b.md"]')
        assert result == ['a.md', 'b.md']


# ---------------------------------------------------------------------------
# _select_via_model
# ---------------------------------------------------------------------------


class TestSelectViaModel:
    @pytest.mark.asyncio
    async def test_returns_selected_headers(self):
        headers = [_make_header('u', 'a.md'), _make_header('u', 'b.md')]

        # Mock pydantic-ai Agent.run() to return a selection
        mock_result = AsyncMock()
        mock_result.output = '["a.md"]'

        with patch('marcel_core.memory.selector.Agent') as MockAgent:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_result)
            MockAgent.return_value = mock_agent_instance

            result = await _select_via_model('find a', headers)

        assert len(result) == 1
        assert result[0].filename == 'a.md'

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self):
        headers = [_make_header('u', f'{i}.md') for i in range(5)]

        with patch('marcel_core.memory.selector.Agent') as MockAgent:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(side_effect=RuntimeError('Model down'))
            MockAgent.return_value = mock_agent_instance

            result = await _select_via_model('query', headers)

        # Falls back to first MAX_SELECTED headers
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_limits_to_max_selected(self):
        import json

        headers = [_make_header('u', f'{i}.md') for i in range(20)]
        filenames = [f'{i}.md' for i in range(20)]

        mock_result = AsyncMock()
        mock_result.output = json.dumps(filenames)

        with patch('marcel_core.memory.selector.Agent') as MockAgent:
            mock_agent_instance = AsyncMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_result)
            MockAgent.return_value = mock_agent_instance

            result = await _select_via_model('query', headers)

        assert len(result) <= MAX_SELECTED


# ---------------------------------------------------------------------------
# select_relevant_memories
# ---------------------------------------------------------------------------


class TestSelectRelevantMemories:
    @pytest.mark.asyncio
    async def test_empty_headers_returns_empty(self):
        with patch('marcel_core.memory.selector.scan_memory_headers', return_value=[]):
            result = await select_relevant_memories('user', 'anything')
        assert result == []

    @pytest.mark.asyncio
    async def test_small_set_loads_all_without_model(self):
        headers = [_make_header('u', f'{i}.md') for i in range(3)]

        with patch('marcel_core.memory.selector.scan_memory_headers', return_value=headers):
            with patch('marcel_core.memory.selector.load_memory_file', return_value='content'):
                with patch('marcel_core.memory.selector._select_via_model') as mock_select:
                    result = await select_relevant_memories('u', 'query', include_household=False)

        mock_select.assert_not_called()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_large_set_uses_model(self):
        headers = [_make_header('u', f'{i}.md') for i in range(SELECTION_THRESHOLD + 2)]

        with patch('marcel_core.memory.selector.scan_memory_headers', return_value=headers):
            with patch('marcel_core.memory.selector.load_memory_file', return_value='content'):
                with patch(
                    'marcel_core.memory.selector._select_via_model', AsyncMock(return_value=headers[:2])
                ) as mock_select:
                    result = await select_relevant_memories('u', 'query', include_household=False)

        mock_select.assert_called_once()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_freshness_note_appended_when_set(self):
        headers = [_make_header('u', 'old.md', mtime=1.0)]

        with patch('marcel_core.memory.selector.scan_memory_headers', return_value=headers):
            with patch('marcel_core.memory.selector.load_memory_file', return_value='content'):
                with patch('marcel_core.memory.selector.memory_freshness_note', return_value='(old)'):
                    result = await select_relevant_memories('u', 'q', include_household=False)

        assert len(result) == 1
        _, content = result[0]
        assert '(old)' in content

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self):
        headers = [_make_header('u', 'empty.md')]

        with patch('marcel_core.memory.selector.scan_memory_headers', return_value=headers):
            with patch('marcel_core.memory.selector.load_memory_file', return_value='   '):
                result = await select_relevant_memories('u', 'q', include_household=False)

        assert result == []

    @pytest.mark.asyncio
    async def test_household_included(self):
        user_headers = [_make_header('u', 'user.md')]
        household_headers = [_make_header('_household', 'house.md')]

        def fake_scan(slug):
            return household_headers if slug == '_household' else user_headers

        with patch('marcel_core.memory.selector.scan_memory_headers', side_effect=fake_scan):
            with patch('marcel_core.memory.selector.load_memory_file', return_value='content'):
                result = await select_relevant_memories('u', 'q', include_household=True)

        assert len(result) == 2
