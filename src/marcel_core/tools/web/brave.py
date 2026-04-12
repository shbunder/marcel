"""Brave Search API backend.

Uses the public Brave Search API at ``api.search.brave.com``. Primary
backend for :func:`~marcel_core.tools.web.backends.select_backend` when
``BRAVE_API_KEY`` is configured.

Docs: https://brave.com/search/api/
Free tier: 1000 queries/month (plus any user-configured cap), 1 query/sec.
"""

from __future__ import annotations

import logging

import httpx

from marcel_core.tools.web.backends import SearchBackend, SearchBackendError, SearchResult

log = logging.getLogger(__name__)

_BRAVE_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_USER_AGENT = 'Marcel/1.0 (+https://github.com/anthropics/claude-code)'


class BraveBackend(SearchBackend):
    """Brave Search API client.

    Stateless aside from the configured API key — safe to share across
    concurrent turns.
    """

    name = 'brave'

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        params = {
            'q': query,
            'count': str(max(1, min(max_results, 20))),
            'safesearch': 'moderate',
        }
        headers = {
            'X-Subscription-Token': self._api_key,
            'Accept': 'application/json',
            'User-Agent': _USER_AGENT,
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(_BRAVE_ENDPOINT, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SearchBackendError(f'network failure — {exc}') from exc

        if resp.status_code == 401:
            raise SearchBackendError('Brave API key invalid or revoked')
        if resp.status_code == 429:
            raise SearchBackendError('Brave rate limit — slow down')
        if resp.status_code >= 400:
            raise SearchBackendError(f'Brave HTTP {resp.status_code}')

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SearchBackendError(f'Brave returned invalid JSON — {exc}') from exc

        web_block = payload.get('web') or {}
        raw_results = web_block.get('results') or []
        results: list[SearchResult] = []
        for raw in raw_results[:max_results]:
            title = _strip(raw.get('title'))
            url = _strip(raw.get('url'))
            snippet = _strip(raw.get('description'))
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet=snippet))

        return results


def _strip(value: object) -> str:
    """Convert a possibly-None API field to a trimmed string."""
    if value is None:
        return ''
    return str(value).strip()
