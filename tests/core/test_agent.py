"""Tests for agent module — memory extraction and selection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from marcel_core.memory.extract import _parse_operations, extract_and_save_memories
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# memory_extract.py — pydantic-ai agent-based extraction
# ---------------------------------------------------------------------------


class TestExtractAndSaveMemories:
    def test_writes_memory_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        mock_result = MagicMock()
        mock_result.output = '[{"action": "create", "filename": "tea.md", "content": "---\\nname: tea\\ntype: preference\\n---\\nLikes tea."}]'

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch('marcel_core.memory.extract.Agent', return_value=mock_agent):
            asyncio.run(extract_and_save_memories('shaun', 'I like tea', 'Noted!', 'conv-1'))

        # Memory file should be written
        mem_file = tmp_path / 'users' / 'shaun' / 'memory' / 'tea.md'
        assert mem_file.exists()
        assert 'Likes tea' in mem_file.read_text()

    def test_includes_manifest_in_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'prefs', '---\nname: prefs\ntype: preference\n---\nLikes tea.')

        captured_prompt = {}
        mock_result = MagicMock()
        mock_result.output = '[]'

        mock_agent = MagicMock()

        async def _capture_run(prompt, **kwargs):
            captured_prompt['text'] = prompt
            return mock_result

        mock_agent.run = _capture_run

        with patch('marcel_core.memory.extract.Agent', return_value=mock_agent):
            asyncio.run(extract_and_save_memories('shaun', 'hello', 'hi', 'conv-1'))

        assert 'prefs.md' in captured_prompt['text']

    def test_disabled_flag_skips_extraction_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.config import settings

        monkeypatch.setattr(settings, 'marcel_memory_extractor_enabled', False)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=AssertionError('extractor must not run'))

        with patch('marcel_core.memory.extract.Agent', return_value=mock_agent):
            asyncio.run(extract_and_save_memories('shaun', 'I like tea', 'Noted!', 'conv-1'))

        assert not (tmp_path / 'users' / 'shaun' / 'memory').exists()

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError('api down'))

        with patch('marcel_core.memory.extract.Agent', return_value=mock_agent):
            # Should not raise
            asyncio.run(extract_and_save_memories('shaun', 'x', 'y', 'conv-1'))

    def test_empty_response_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        mock_result = MagicMock()
        mock_result.output = '[]'

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch('marcel_core.memory.extract.Agent', return_value=mock_agent):
            asyncio.run(extract_and_save_memories('shaun', 'hello', 'hi', 'conv-1'))

        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        if mem_dir.exists():
            files = list(mem_dir.glob('*.md'))
            assert len(files) == 0


class TestParseOperations:
    def test_parses_json_array(self):
        ops = _parse_operations('[{"action": "create", "filename": "test.md", "content": "hello"}]')
        assert len(ops) == 1
        assert ops[0]['filename'] == 'test.md'

    def test_handles_empty_array(self):
        assert _parse_operations('[]') == []

    def test_handles_code_fences(self):
        response = '```json\n[{"action": "create", "filename": "test.md", "content": "hello"}]\n```'
        ops = _parse_operations(response)
        assert len(ops) == 1

    def test_handles_non_json(self):
        assert _parse_operations('no memories to save') == []

    def test_filters_non_dicts(self):
        assert _parse_operations('[42, "string", null]') == []
