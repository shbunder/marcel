"""Tests for BraveBackend — mocked httpx, no live network."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marcel_core.tools.web.backends import SearchBackendError
from marcel_core.tools.web.brave import BraveBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BRAVE_RESPONSE = {
    'web': {
        'results': [
            {
                'title': 'Paris\u2013Roubaix 2026 \u2014 Live',
                'url': 'https://www.procyclingstats.com/race/paris-roubaix/2026/live',
                'description': 'Live race updates from the cobbles.',
            },
            {
                'title': 'Paris-Roubaix Results | Cyclingnews',
                'url': 'https://www.cyclingnews.com/races/paris-roubaix-2026/',
                'description': 'Results and coverage for the Hell of the North.',
            },
            {
                'title': 'Broken result without url',
                'url': None,
                'description': 'Should be filtered out.',
            },
        ]
    }
}


def _mock_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _response(status: int, body: dict | str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if isinstance(body, dict):
        resp.json = MagicMock(return_value=body)
        resp.text = json.dumps(body)
    elif isinstance(body, str):
        resp.json = MagicMock(side_effect=ValueError('not json'))
        resp.text = body
    else:
        resp.json = MagicMock(return_value={})
        resp.text = ''
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBraveBackend:
    @pytest.mark.asyncio
    async def test_parses_happy_path(self):
        resp = _response(200, _BRAVE_RESPONSE)
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            results = await backend.search('paris-roubaix 2026', max_results=5)

        assert len(results) == 2
        assert results[0].title.startswith('Paris')
        assert results[0].url.startswith('https://www.procyclingstats.com')
        assert 'cobbles' in results[0].snippet
        assert results[1].url.startswith('https://www.cyclingnews.com')

    @pytest.mark.asyncio
    async def test_passes_auth_header(self):
        resp = _response(200, {'web': {'results': []}})
        client = _mock_client(resp)
        backend = BraveBackend(api_key='secret-123')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            await backend.search('query', max_results=5)

        call_args = client.get.call_args
        headers = call_args.kwargs['headers']
        assert headers['X-Subscription-Token'] == 'secret-123'

    @pytest.mark.asyncio
    async def test_max_results_clamped_to_20(self):
        resp = _response(200, {'web': {'results': []}})
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            await backend.search('query', max_results=100)

        params = client.get.call_args.kwargs['params']
        assert params['count'] == '20'

    @pytest.mark.asyncio
    async def test_max_results_clamped_to_1(self):
        resp = _response(200, {'web': {'results': []}})
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            await backend.search('query', max_results=0)

        params = client.get.call_args.kwargs['params']
        assert params['count'] == '1'

    @pytest.mark.asyncio
    async def test_401_raises_invalid_key(self):
        resp = _response(401)
        client = _mock_client(resp)
        backend = BraveBackend(api_key='bad-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('query', max_results=5)

        assert 'invalid or revoked' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit(self):
        resp = _response(429)
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('query', max_results=5)

        assert 'rate limit' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_500_raises_generic_http_error(self):
        resp = _response(500)
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('query', max_results=5)

        assert 'Brave HTTP 500' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        resp = _response(200, 'not valid json')
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('query', max_results=5)

        assert 'invalid JSON' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_network_error_raises(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError('DNS fail'))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            with pytest.raises(SearchBackendError) as exc_info:
                await backend.search('query', max_results=5)

        assert 'network failure' in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_list(self):
        resp = _response(200, {'web': {'results': []}})
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            results = await backend.search('query', max_results=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_missing_web_block_returns_empty(self):
        resp = _response(200, {})
        client = _mock_client(resp)
        backend = BraveBackend(api_key='test-key')

        with patch('marcel_core.tools.web.brave.httpx.AsyncClient', return_value=client):
            results = await backend.search('query', max_results=5)

        assert results == []
