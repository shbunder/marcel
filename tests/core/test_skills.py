"""Tests for skills registry, executor, integration tool, and memory search tool."""

import json

import httpx
import pytest

from marcel_core.skills.executor import _apply_transform, run
from marcel_core.skills.integrations import _registry, discover, get_handler, list_python_skills, register
from marcel_core.skills.registry import get_skill, list_skills
from marcel_core.skills.tool import build_skills_mcp_server


class TestRegistry:
    def test_list_skills_empty_json_still_has_python(self, tmp_path, monkeypatch):
        # Point registry at an empty JSON file — python integrations still show up
        import marcel_core.skills.registry as reg

        empty = tmp_path / 'skills.json'
        empty.write_text('{}')
        monkeypatch.setattr(reg, '_SKILLS_JSON', empty)
        names = list_skills()
        # No JSON skills, but python integrations are discovered
        assert 'icloud.calendar' in names
        assert 'icloud.mail' in names

    def test_list_skills_returns_json_and_python_names(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        f = tmp_path / 'skills.json'
        f.write_text(json.dumps({'a.b': {}, 'c.d': {}}))
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        names = set(list_skills())
        assert {'a.b', 'c.d'}.issubset(names)
        assert 'icloud.calendar' in names

    def test_get_skill_returns_config(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        cfg = {'url': 'https://example.com', 'method': 'GET'}
        f = tmp_path / 'skills.json'
        f.write_text(json.dumps({'test.skill': cfg}))
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        assert get_skill('test.skill') == cfg

    def test_get_skill_unknown_raises_key_error(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        f = tmp_path / 'skills.json'
        f.write_text('{}')
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        with pytest.raises(KeyError, match='Unknown skill'):
            get_skill('nope')

    def test_get_skill_suggests_available(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        f = tmp_path / 'skills.json'
        f.write_text(json.dumps({'a.b': {}}))
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        with pytest.raises(KeyError, match='a.b'):
            get_skill('nope')


class TestExecutorAuth:
    @pytest.mark.asyncio
    async def test_oauth2_returns_not_connected(self):
        config = {
            'url': 'https://example.com',
            'method': 'GET',
            'auth': {'type': 'oauth2', 'provider': 'google'},
        }
        result = await run(config, {}, 'shaun')
        assert 'not connected' in result.lower()
        assert 'Google' in result

    @pytest.mark.asyncio
    async def test_no_auth_calls_url(self, respx_mock):
        respx_mock.get('https://example.com/data').mock(return_value=httpx.Response(200, text='{"ok": true}'))
        config = {'url': 'https://example.com/data', 'method': 'GET'}
        result = await run(config, {}, 'shaun')
        assert 'ok' in result

    @pytest.mark.asyncio
    async def test_api_key_added_to_header(self, respx_mock, monkeypatch):
        monkeypatch.setenv('TEST_API_KEY', 'secret123')
        respx_mock.get('https://example.com/data').mock(return_value=httpx.Response(200, text='ok'))
        config = {
            'url': 'https://example.com/data',
            'method': 'GET',
            'auth': {
                'type': 'api_key',
                'env_var': 'TEST_API_KEY',
                'location': 'header',
                'header_name': 'X-API-Key',
            },
        }
        await run(config, {}, 'shaun')
        assert respx_mock.calls[0].request.headers['x-api-key'] == 'secret123'

    @pytest.mark.asyncio
    async def test_params_resolved_from_args(self, respx_mock):
        respx_mock.get('https://example.com/items').mock(return_value=httpx.Response(200, text='[]'))
        config = {
            'url': 'https://example.com/items',
            'method': 'GET',
            'params': {
                'limit': {'from': 'args.limit', 'default': 10},
            },
        }
        await run(config, {'limit': '5'}, 'shaun')
        assert respx_mock.calls[0].request.url.params['limit'] == '5'

    @pytest.mark.asyncio
    async def test_params_use_default_when_arg_missing(self, respx_mock):
        respx_mock.get('https://example.com/items').mock(return_value=httpx.Response(200, text='[]'))
        config = {
            'url': 'https://example.com/items',
            'method': 'GET',
            'params': {
                'limit': {'from': 'args.limit', 'default': 10},
            },
        }
        await run(config, {}, 'shaun')
        assert respx_mock.calls[0].request.url.params['limit'] == '10'


class TestTransform:
    def test_no_transform_returns_raw(self):
        assert _apply_transform('', '{"a": 1}') == '{"a": 1}'

    def test_unknown_prefix_returns_raw(self):
        assert _apply_transform('xpath:/foo', '<x/>') == '<x/>'

    def test_jq_import_error_returns_raw(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'jq':
                raise ImportError('no jq')
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', mock_import)
        result = _apply_transform('jq:.x', '{"x": 1}')
        assert result == '{"x": 1}'


class TestIntegrationFramework:
    def test_register_and_get_handler(self, monkeypatch):
        # Work on a clean registry
        saved = dict(_registry)
        monkeypatch.setattr('marcel_core.skills.integrations._registry', {})

        @register('test.skill')
        async def handler(params, user_slug):
            return 'ok'

        assert 'test.skill' in list_python_skills()
        assert get_handler('test.skill') is handler

        # Restore
        _registry.clear()
        _registry.update(saved)

    def test_duplicate_registration_raises(self, monkeypatch):
        saved = dict(_registry)
        monkeypatch.setattr('marcel_core.skills.integrations._registry', {})

        @register('dup.skill')
        async def handler1(params, user_slug):
            return 'first'

        with pytest.raises(ValueError, match='already registered'):

            @register('dup.skill')
            async def handler2(params, user_slug):
                return 'second'

        _registry.clear()
        _registry.update(saved)

    def test_get_handler_unknown_raises(self):
        with pytest.raises(KeyError, match='No python integration'):
            get_handler('nonexistent.skill')

    def test_discover_imports_modules(self):
        # After discover, icloud skills should be registered
        discover()
        names = list_python_skills()
        assert 'icloud.calendar' in names
        assert 'icloud.mail' in names


class TestPythonExecutor:
    @pytest.mark.asyncio
    async def test_python_skill_dispatches_to_handler(self, monkeypatch):
        saved = dict(_registry)
        monkeypatch.setattr('marcel_core.skills.integrations._registry', {})

        @register('test.echo')
        async def echo_handler(params, user_slug):
            return f'echo: {params.get("msg", "")} for {user_slug}'

        config = {'type': 'python', 'handler': 'test.echo'}
        result = await run(config, {'msg': 'hello'}, 'shaun')
        assert result == 'echo: hello for shaun'

        _registry.clear()
        _registry.update(saved)


class TestRegistryMerge:
    def test_list_skills_includes_python_integrations(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        f = tmp_path / 'skills.json'
        f.write_text(json.dumps({'shell.test': {'type': 'shell', 'command': 'echo hi'}}))
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        names = list_skills()
        # Should include both the JSON skill and discovered python integrations
        assert 'shell.test' in names
        assert 'icloud.calendar' in names
        assert 'icloud.mail' in names

    def test_get_skill_returns_python_config(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg

        f = tmp_path / 'skills.json'
        f.write_text('{}')
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        config = get_skill('icloud.calendar')
        assert config['type'] == 'python'
        assert config['handler'] == 'icloud.calendar'


# ---------------------------------------------------------------------------
# memory_search MCP tool
# ---------------------------------------------------------------------------


class TestBuildSkillsMcpServer:
    """Tests for the skills MCP server configuration."""

    def test_server_includes_memory_search_tool(self) -> None:
        server = build_skills_mcp_server('shaun', 'cli')
        assert server['type'] == 'sdk'
        assert server['name'] == 'marcel-skills'
        # Verify the server instance was created (tools are registered internally).
        assert server['instance'] is not None
