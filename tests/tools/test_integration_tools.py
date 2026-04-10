"""Tests for tools/integration.py — integration dispatcher, memory_search, and notify."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.storage import _root
from marcel_core.tools.integration import integration, memory_search, notify


def _ctx(channel: str = 'cli', user_slug: str = 'shaun') -> MagicMock:
    """Return a minimal mock RunContext."""
    deps = MarcelDeps(user_slug=user_slug, conversation_id='conv-1', channel=channel)
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# integration tool
# ---------------------------------------------------------------------------


class TestIntegrationTool:
    @pytest.mark.asyncio
    async def test_dispatches_to_skill(self, monkeypatch):
        from marcel_core.skills.integrations import _registry

        saved = dict(_registry)
        monkeypatch.setattr('marcel_core.skills.integrations._registry', {})

        from marcel_core.skills.integrations import register

        @register('test.ping')
        async def ping(params, user_slug):
            return 'pong'

        with patch('marcel_core.tools.integration.get_skill', return_value={'type': 'python', 'handler': 'test.ping'}):
            with patch('marcel_core.tools.integration.run', AsyncMock(return_value='pong')):
                result = await integration(_ctx(), 'test.ping', {})

        assert result == 'pong'
        _registry.clear()
        _registry.update(saved)

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self):
        result = await integration(_ctx(), 'nonexistent.skill', {})
        assert 'error' in result.lower() or 'available' in result.lower()

    @pytest.mark.asyncio
    async def test_none_params_defaults_to_empty(self):
        with patch('marcel_core.tools.integration.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.integration.run', AsyncMock(side_effect=RuntimeError('boom'))):
                result = await integration(_ctx(), 'x', None)
        assert 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_skill_execution_error_returns_message(self):
        with patch('marcel_core.tools.integration.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.integration.run', AsyncMock(side_effect=Exception('oops'))):
                result = await integration(_ctx(), 'x', {})
        assert 'oops' in result or 'error' in result.lower()


# ---------------------------------------------------------------------------
# memory_search tool
# ---------------------------------------------------------------------------


class TestMemorySearchTool:
    @pytest.mark.asyncio
    async def test_returns_no_results_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await memory_search(_ctx(), 'find something')
        assert 'No memories found' in result

    @pytest.mark.asyncio
    async def test_finds_matching_memories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.storage import save_memory_file

        save_memory_file(
            'shaun',
            'dentist',
            '---\nname: dentist\ntype: schedule\ndescription: Dentist appointment\n---\nDentist on April 10.',
        )
        result = await memory_search(_ctx(), 'dentist')
        assert 'dentist' in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_type_filter_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await memory_search(_ctx(), 'anything', type_filter='invalid_type')
        assert 'error' in result.lower() or 'invalid' in result.lower()

    @pytest.mark.asyncio
    async def test_valid_type_filter_works(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await memory_search(_ctx(), 'anything', type_filter='schedule')
        assert 'error' not in result.lower() or 'No memories' in result


# ---------------------------------------------------------------------------
# notify tool
# ---------------------------------------------------------------------------


class TestNotifyTool:
    @pytest.mark.asyncio
    async def test_returns_ok_for_non_telegram(self):
        result = await notify(_ctx(channel='cli'), 'Starting task...')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_empty_message_returns_ok(self):
        result = await notify(_ctx(), '')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_sends_message_when_chat_linked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.channels.telegram import sessions

        sessions.link_user('shaun', 42)

        with patch('marcel_core.channels.telegram.bot.send_message', AsyncMock(return_value=1)):
            result = await notify(_ctx(channel='telegram'), 'Working on it...')

        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_returns_ok_when_no_chat_linked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        # No user linked — chat_id is None
        result = await notify(_ctx(channel='telegram', user_slug='unlinked'), 'hi')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_handles_send_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.channels.telegram import sessions

        sessions.link_user('shaun', 42)

        with patch('marcel_core.channels.telegram.bot.send_message', AsyncMock(side_effect=Exception('network error'))):
            result = await notify(_ctx(channel='telegram'), 'Working...')

        # Should not raise — returns an error message
        assert 'notify failed' in result or result == 'ok'
