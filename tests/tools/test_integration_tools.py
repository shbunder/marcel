"""Tests for tools/integration.py and tools/marcel.py — integration dispatcher, memory, notify."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.storage import _root
from marcel_core.tools.marcel import marcel
from marcel_core.tools.toolkit import integration


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
        from marcel_core.toolkit import _registry

        saved = dict(_registry)
        monkeypatch.setattr('marcel_core.toolkit._registry', {})

        from marcel_core.toolkit import register

        @register('test.ping')
        async def ping(params, user_slug):
            return 'pong'

        with patch('marcel_core.tools.toolkit.get_skill', return_value={'type': 'python', 'handler': 'test.ping'}):
            with patch('marcel_core.tools.toolkit.run', AsyncMock(return_value='pong')):
                result = await integration(_ctx(), 'test.ping', {})

        assert 'pong' in result
        _registry.clear()
        _registry.update(saved)

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self):
        result = await integration(_ctx(), 'nonexistent.skill', {})
        assert 'error' in result.lower() or 'available' in result.lower()

    @pytest.mark.asyncio
    async def test_none_params_defaults_to_empty(self):
        with patch('marcel_core.tools.toolkit.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.toolkit.run', AsyncMock(side_effect=RuntimeError('boom'))):
                result = await integration(_ctx(), 'x', None)
        assert 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_skill_execution_error_returns_message(self):
        with patch('marcel_core.tools.toolkit.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.toolkit.run', AsyncMock(side_effect=Exception('oops'))):
                result = await integration(_ctx(), 'x', {})
        assert 'oops' in result or 'error' in result.lower()

    @pytest.mark.asyncio
    async def test_auto_injects_skill_docs_on_first_call(self):
        """When skill docs haven't been read yet, integration prepends them."""
        ctx = _ctx()
        with patch('marcel_core.tools.toolkit.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.toolkit.run', AsyncMock(return_value='result-data')):
                with patch('marcel_core.skills.loader.get_skill_content', return_value='Full banking docs here'):
                    result = await integration(ctx, 'banking.balance', {})

        assert 'Auto-loaded banking skill docs' in result
        assert 'Full banking docs here' in result
        assert 'result-data' in result
        # Second call should NOT auto-inject
        assert 'banking' in ctx.deps.turn.read_skills

    @pytest.mark.asyncio
    async def test_no_duplicate_inject_after_read_skill(self):
        """If skill was read via marcel tool first, integration doesn't re-inject."""
        ctx = _ctx()
        ctx.deps.turn.read_skills.add('banking')
        with patch('marcel_core.tools.toolkit.get_skill', return_value={'type': 'python', 'handler': 'x'}):
            with patch('marcel_core.tools.toolkit.run', AsyncMock(return_value='result-data')):
                result = await integration(ctx, 'banking.balance', {})

        assert 'Auto-loaded' not in result
        assert result == 'result-data'


# ---------------------------------------------------------------------------
# marcel tool — search_memory action
# ---------------------------------------------------------------------------


class TestMarcelSearchMemory:
    @pytest.mark.asyncio
    async def test_returns_no_results_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(), 'search_memory', query='find something')
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
        result = await marcel(_ctx(), 'search_memory', query='dentist')
        assert 'dentist' in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_type_filter_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(), 'search_memory', query='anything', type_filter='invalid_type')
        assert 'error' in result.lower() or 'invalid' in result.lower()

    @pytest.mark.asyncio
    async def test_valid_type_filter_works(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(), 'search_memory', query='anything', type_filter='schedule')
        assert 'error' not in result.lower() or 'No memories' in result

    @pytest.mark.asyncio
    async def test_missing_query_returns_error(self):
        result = await marcel(_ctx(), 'search_memory')
        assert 'error' in result.lower()


# ---------------------------------------------------------------------------
# marcel tool — notify action
# ---------------------------------------------------------------------------


class TestMarcelNotify:
    @pytest.mark.asyncio
    async def test_returns_ok_for_non_telegram(self):
        result = await marcel(_ctx(channel='cli'), 'notify', message='Starting task...')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_empty_message_returns_ok(self):
        result = await marcel(_ctx(), 'notify', message='')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_sends_message_when_chat_linked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        import importlib

        # Telegram lives in the zoo since ISSUE-7d6b3f stage 4c; resolve
        # at runtime via the conftest alias rather than a static import.
        sessions = importlib.import_module('marcel_core.channels.telegram.sessions')

        sessions.link_user('shaun', 42)

        with patch('marcel_core.channels.telegram.bot.send_message', AsyncMock(return_value=1)):
            result = await marcel(_ctx(channel='telegram'), 'notify', message='Working on it...')

        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_returns_ok_when_no_chat_linked(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(channel='telegram', user_slug='unlinked'), 'notify', message='hi')
        assert result == 'ok'

    @pytest.mark.asyncio
    async def test_telegram_handles_send_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        import importlib

        # Telegram lives in the zoo since ISSUE-7d6b3f stage 4c; resolve
        # at runtime via the conftest alias rather than a static import.
        sessions = importlib.import_module('marcel_core.channels.telegram.sessions')

        sessions.link_user('shaun', 42)

        with patch('marcel_core.channels.telegram.bot.send_message', AsyncMock(side_effect=Exception('network error'))):
            result = await marcel(_ctx(channel='telegram'), 'notify', message='Working...')

        assert 'notify failed' in result or result == 'ok'


# ---------------------------------------------------------------------------
# marcel tool — read_skill action
# ---------------------------------------------------------------------------


class TestMarcelReadSkill:
    @pytest.mark.asyncio
    async def test_returns_skill_content(self):
        ctx = _ctx()
        with patch('marcel_core.skills.loader.get_skill_content', return_value='Full skill documentation here'):
            result = await marcel(ctx, 'read_skill', name='banking')
        assert 'Full skill documentation here' in result
        assert 'banking' in ctx.deps.turn.read_skills

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self):
        with patch('marcel_core.skills.loader.get_skill_content', return_value=None):
            with patch('marcel_core.skills.loader.load_skills', return_value=[]):
                result = await marcel(_ctx(), 'read_skill', name='nonexistent')
        assert 'Unknown skill' in result

    @pytest.mark.asyncio
    async def test_missing_name_returns_error(self):
        result = await marcel(_ctx(), 'read_skill')
        assert 'error' in result.lower()


# ---------------------------------------------------------------------------
# marcel tool — settings actions
# ---------------------------------------------------------------------------


class TestMarcelSettings:
    @pytest.mark.asyncio
    async def test_list_models_returns_models(self):
        result = await marcel(_ctx(), 'list_models')
        assert 'Available models' in result
        assert 'anthropic:claude-sonnet-4-6' in result

    @pytest.mark.asyncio
    async def test_get_model_defaults_to_current_channel(self):
        result = await marcel(_ctx(channel='telegram'), 'get_model')
        assert 'telegram' in result

    @pytest.mark.asyncio
    async def test_get_model_with_explicit_channel(self):
        result = await marcel(_ctx(), 'get_model', name='cli')
        assert 'cli' in result

    @pytest.mark.asyncio
    async def test_set_model_valid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(), 'set_model', name='telegram:anthropic:claude-sonnet-4-6')
        assert 'telegram' in result
        assert 'anthropic:claude-sonnet-4-6' in result

    @pytest.mark.asyncio
    async def test_set_model_rejects_unqualified(self):
        result = await marcel(_ctx(), 'set_model', name='telegram:claude-sonnet-4-6')
        assert 'Error' in result
        assert 'fully qualified' in result

    @pytest.mark.asyncio
    async def test_set_model_accepts_off_registry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(
            _ctx(),
            'set_model',
            name='telegram:anthropic:claude-3-5-sonnet-latest',
        )
        assert 'Error' not in result
        assert 'anthropic:claude-3-5-sonnet-latest' in result

    @pytest.mark.asyncio
    async def test_set_model_missing_colon(self):
        result = await marcel(_ctx(), 'set_model', name='telegram')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_set_model_missing_value(self):
        result = await marcel(_ctx(), 'set_model')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# marcel tool — render action
# ---------------------------------------------------------------------------


def _patch_registry(monkeypatch, components: list) -> None:
    """Replace build_registry with a stub that returns the given components."""
    from marcel_core.skills.component_registry import ComponentRegistry

    registry = ComponentRegistry(components)
    monkeypatch.setattr('marcel_core.skills.component_registry.build_registry', lambda _slug: registry)


class TestMarcelRender:
    @pytest.mark.asyncio
    async def test_missing_component_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        result = await marcel(_ctx(), 'render', props={'transactions': []})
        assert 'render failed' in result
        assert 'component' in result

    @pytest.mark.asyncio
    async def test_unknown_component_lists_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.skills.components import ComponentSchema

        _patch_registry(monkeypatch, [ComponentSchema(name='calendar', skill='ui')])

        result = await marcel(_ctx(), 'render', component='does_not_exist', props={})
        assert 'unknown component' in result
        assert 'calendar' in result  # lists available

    @pytest.mark.asyncio
    async def test_valid_component_creates_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.skills.components import ComponentSchema
        from marcel_core.storage.artifacts import load_artifact

        _patch_registry(monkeypatch, [ComponentSchema(name='transaction_list', skill='banking')])

        result = await marcel(
            _ctx(channel='cli'),
            'render',
            component='transaction_list',
            props={'transactions': [{'date': '2026-04-11', 'description': 'Colruyt', 'amount': -42.18}]},
        )
        assert 'rendered' in result
        assert 'transaction_list' in result

        # Pull the artifact id out of the confirmation and verify it was saved
        import re

        match = re.search(r'artifact (\w+)', result)
        assert match is not None
        loaded = load_artifact(match.group(1))
        assert loaded is not None
        assert loaded.content_type == 'a2ui'
        assert loaded.component_name == 'transaction_list'
        assert '"Colruyt"' in loaded.content

    @pytest.mark.asyncio
    async def test_telegram_sends_mini_app_button(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.config.settings.marcel_public_url', 'https://example.invalid')
        import importlib

        # Telegram lives in the zoo since ISSUE-7d6b3f stage 4c; resolve
        # at runtime via the conftest alias rather than a static import.
        sessions = importlib.import_module('marcel_core.channels.telegram.sessions')
        from marcel_core.skills.components import ComponentSchema

        sessions.link_user('shaun', 42)
        _patch_registry(monkeypatch, [ComponentSchema(name='transaction_list', skill='banking')])

        mock_send = AsyncMock(return_value=1)
        with patch('marcel_core.channels.telegram.bot.send_message', mock_send):
            result = await marcel(
                _ctx(channel='telegram'),
                'render',
                component='transaction_list',
                props={'transactions': []},
            )

        assert 'Mini App button sent' in result
        assert mock_send.await_count == 1
        # Verify the call included a reply_markup with a web_app button
        _, kwargs = mock_send.call_args
        markup = kwargs.get('reply_markup')
        assert markup is not None
        assert 'inline_keyboard' in markup
        assert 'web_app' in markup['inline_keyboard'][0][0]

    @pytest.mark.asyncio
    async def test_telegram_without_public_url_still_creates_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr('marcel_core.config.settings.marcel_public_url', '')
        import importlib

        # Telegram lives in the zoo since ISSUE-7d6b3f stage 4c; resolve
        # at runtime via the conftest alias rather than a static import.
        sessions = importlib.import_module('marcel_core.channels.telegram.sessions')
        from marcel_core.skills.components import ComponentSchema

        sessions.link_user('shaun', 42)
        _patch_registry(monkeypatch, [ComponentSchema(name='transaction_list', skill='banking')])

        result = await marcel(
            _ctx(channel='telegram'),
            'render',
            component='transaction_list',
            props={'transactions': []},
        )
        # No button was sent (no public URL), but the artifact was still created
        assert 'rendered' in result
        assert 'transaction_list' in result

    @pytest.mark.asyncio
    async def test_non_serializable_props_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        from marcel_core.skills.components import ComponentSchema

        _patch_registry(monkeypatch, [ComponentSchema(name='transaction_list', skill='banking')])

        # A set is not JSON-serializable, but the render action uses default=str
        # so it must fall through gracefully; use a circular reference instead.
        circular: dict = {}
        circular['self'] = circular

        result = await marcel(_ctx(), 'render', component='transaction_list', props=circular)
        assert 'render failed' in result


# ---------------------------------------------------------------------------
# marcel tool — unknown action
# ---------------------------------------------------------------------------


class TestMarcelUnknownAction:
    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_action(self):
        result = await marcel(_ctx(), 'does_not_exist')
        assert 'Unknown action' in result
        assert 'read_skill' in result  # lists available actions
        assert 'render' in result  # render is advertised too
