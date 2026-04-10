"""Tests for agent module — memory extraction and selection."""

import asyncio

import claude_agent_sdk
from claude_agent_sdk import ResultMessage

from marcel_core.agent.memory_extract import extract_and_save_memories
from marcel_core.memory.selector import _parse_selection, select_relevant_memories
from marcel_core.storage import _root

# ---------------------------------------------------------------------------
# memory_extract.py — agent-based extraction
# ---------------------------------------------------------------------------


class TestExtractAndSaveMemories:
    def test_calls_query_with_correct_options(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        captured_kwargs = {}

        async def capture_query(**kwargs):
            captured_kwargs.update(kwargs)
            yield ResultMessage(
                subtype='success',
                duration_ms=50,
                duration_api_ms=40,
                is_error=False,
                num_turns=1,
                session_id='extract-1',
                total_cost_usd=0.001,
            )

        monkeypatch.setattr(claude_agent_sdk, 'query', capture_query)
        asyncio.run(extract_and_save_memories('shaun', 'I like tea', 'Noted!', 'conv-1'))

        # Verify correct model and tools preset.
        opts = captured_kwargs.get('options')
        assert opts is not None
        assert opts.model == 'claude-haiku-4-5-20251001'
        assert opts.tools == {'type': 'preset', 'preset': 'claude_code'}
        assert opts.max_turns == 3
        # CWD should be user's memory dir.
        expected_cwd = str(tmp_path / 'users' / 'shaun' / 'memory')
        assert opts.cwd == expected_cwd
        # Prompt should include the user/assistant text.
        assert 'I like tea' in captured_kwargs.get('prompt', '')
        assert 'Noted!' in captured_kwargs.get('prompt', '')

    def test_includes_manifest_in_system_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'prefs', '---\nname: prefs\ntype: preference\n---\nLikes tea.')

        captured_kwargs = {}

        async def capture_query(**kwargs):
            captured_kwargs.update(kwargs)
            yield ResultMessage(
                subtype='success',
                duration_ms=50,
                duration_api_ms=40,
                is_error=False,
                num_turns=1,
                session_id='s',
                total_cost_usd=0.001,
            )

        monkeypatch.setattr(claude_agent_sdk, 'query', capture_query)
        asyncio.run(extract_and_save_memories('shaun', 'hello', 'hi', 'conv-1'))

        # System prompt should contain the existing memory manifest.
        system = captured_kwargs['options'].system_prompt
        assert 'prefs.md' in system
        assert '[preference]' in system

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)

        async def boom(**_):
            raise RuntimeError('api down')
            yield  # make it an async generator  # noqa: RET503

        monkeypatch.setattr(claude_agent_sdk, 'query', boom)
        # Should not raise
        asyncio.run(extract_and_save_memories('shaun', 'x', 'y', 'conv-1'))


# ---------------------------------------------------------------------------
# memory_select.py — relevance selection
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_parses_json_array(self):
        assert _parse_selection('["calendar.md", "family.md"]') == ['calendar.md', 'family.md']

    def test_parses_empty_array(self):
        assert _parse_selection('[]') == []

    def test_handles_code_fences(self):
        response = '```json\n["calendar.md"]\n```'
        assert _parse_selection(response) == ['calendar.md']

    def test_handles_non_json(self):
        assert _parse_selection('I think calendar.md is relevant') == []

    def test_filters_non_strings(self):
        assert _parse_selection('[42, "valid.md", null]') == ['valid.md']


class TestSelectRelevantMemories:
    def test_returns_empty_for_no_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = asyncio.run(select_relevant_memories('shaun', 'hello'))
        assert result == []

    def test_loads_all_for_small_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'calendar', '---\nname: cal\ntype: schedule\n---\nDentist Friday.')
        save_memory_file('shaun', 'prefs', '---\nname: prefs\ntype: preference\n---\nLikes tea.')

        result = asyncio.run(select_relevant_memories('shaun', 'what do I like?'))
        assert len(result) == 2
        contents = [c for _, c in result]
        assert any('Dentist Friday.' in c for c in contents)
        assert any('Likes tea.' in c for c in contents)

    def test_includes_household_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'personal', 'My stuff.')
        save_memory_file('_household', 'wifi', '---\ntype: household\n---\nPassword: 12345.')

        result = asyncio.run(select_relevant_memories('shaun', 'wifi password'))
        assert len(result) == 2
        contents = [c for _, c in result]
        assert any('Password: 12345.' in c for c in contents)

    def test_excludes_household_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file('shaun', 'personal', 'My stuff.')
        save_memory_file('_household', 'wifi', 'Password: 12345.')

        result = asyncio.run(select_relevant_memories('shaun', 'wifi', include_household=False))
        assert len(result) == 1
        assert 'My stuff.' in result[0][1]
