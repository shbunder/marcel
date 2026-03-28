"""Tests for ISSUE-004: skills registry, executor, and cmd tool."""
import json
import pytest
import httpx

from marcel_core.skills.registry import get_skill, list_skills
from marcel_core.skills.executor import run, _apply_transform


class TestRegistry:
    def test_list_skills_empty(self, tmp_path, monkeypatch):
        # Point registry at an empty JSON file
        import marcel_core.skills.registry as reg
        empty = tmp_path / 'skills.json'
        empty.write_text('{}')
        monkeypatch.setattr(reg, '_SKILLS_JSON', empty)
        assert list_skills() == []

    def test_list_skills_returns_names(self, tmp_path, monkeypatch):
        import marcel_core.skills.registry as reg
        f = tmp_path / 'skills.json'
        f.write_text(json.dumps({'a.b': {}, 'c.d': {}}))
        monkeypatch.setattr(reg, '_SKILLS_JSON', f)
        assert set(list_skills()) == {'a.b', 'c.d'}

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
        respx_mock.get('https://example.com/data').mock(
            return_value=httpx.Response(200, text='{"ok": true}')
        )
        config = {'url': 'https://example.com/data', 'method': 'GET'}
        result = await run(config, {}, 'shaun')
        assert 'ok' in result

    @pytest.mark.asyncio
    async def test_api_key_added_to_header(self, respx_mock, monkeypatch):
        monkeypatch.setenv('TEST_API_KEY', 'secret123')
        respx_mock.get('https://example.com/data').mock(
            return_value=httpx.Response(200, text='ok')
        )
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
        respx_mock.get('https://example.com/items').mock(
            return_value=httpx.Response(200, text='[]')
        )
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
        respx_mock.get('https://example.com/items').mock(
            return_value=httpx.Response(200, text='[]')
        )
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
