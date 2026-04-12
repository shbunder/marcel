"""Tests for Brave→DuckDuckGo transparent failover on rate limit.

These cover the ``_run_with_failover`` behaviour in ``search.py``:

- Brave 429 triggers a retry against DuckDuckGo, the model sees DDG results
  with ``(via duckduckgo)`` in the output (no "Search error" string).
- If DDG also fails, the error message combines both: "Brave rate limit;
  DuckDuckGo fallback failed — ...".
- Non-429 Brave errors (401, 5xx, network) do NOT fail over — they
  surface as "Search error: ..." to the model unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.tools.web.backends import SearchBackendError, SearchResult
from marcel_core.tools.web.dispatcher import web


def _ctx() -> MagicMock:
    deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
    deps.turn = TurnState()
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _brave_mock(side_effect=None, return_value=None) -> MagicMock:
    backend = MagicMock()
    backend.name = 'brave'
    if side_effect is not None:
        backend.search = AsyncMock(side_effect=side_effect)
    else:
        backend.search = AsyncMock(return_value=return_value)
    return backend


def _ddg_mock(side_effect=None, return_value=None) -> MagicMock:
    backend = MagicMock()
    backend.name = 'duckduckgo'
    if side_effect is not None:
        backend.search = AsyncMock(side_effect=side_effect)
    else:
        backend.search = AsyncMock(return_value=return_value)
    return backend


class TestBraveRateLimitFailover:
    @pytest.mark.asyncio
    async def test_brave_429_fails_over_to_ddg_successfully(self):
        brave = _brave_mock(side_effect=SearchBackendError('Brave rate limit — slow down'))
        ddg = _ddg_mock(
            return_value=[
                SearchResult(
                    title='Fallback result',
                    url='https://example.com/ddg',
                    snippet='From the DDG fallback.',
                )
            ]
        )

        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=brave),
            patch('marcel_core.tools.web.duckduckgo.DuckDuckGoBackend', return_value=ddg),
        ):
            result = await web(_ctx(), action='search', query='paris roubaix')

        # User-visible result must NOT be an error — it's the DDG payload
        assert 'Search error' not in result
        assert '(via duckduckgo)' in result
        assert 'Fallback result' in result
        assert 'https://example.com/ddg' in result
        brave.search.assert_awaited_once()
        ddg.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_brave_429_and_ddg_also_fails_returns_combined_error(self):
        brave = _brave_mock(side_effect=SearchBackendError('Brave rate limit — slow down'))
        ddg = _ddg_mock(
            side_effect=SearchBackendError('DuckDuckGo bot challenge — set BRAVE_API_KEY for reliable search')
        )

        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=brave),
            patch('marcel_core.tools.web.duckduckgo.DuckDuckGoBackend', return_value=ddg),
        ):
            result = await web(_ctx(), action='search', query='paris roubaix')

        assert 'Search error: Brave rate limit; DuckDuckGo fallback failed' in result
        assert 'bot challenge' in result

    @pytest.mark.asyncio
    async def test_brave_401_does_not_fail_over(self):
        """Invalid-key errors are a config issue — surface them, don't mask them."""
        brave = _brave_mock(side_effect=SearchBackendError('Brave API key invalid or revoked'))

        ddg_called = False

        def _ddg_factory():
            nonlocal ddg_called
            ddg_called = True
            return _ddg_mock(return_value=[])

        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=brave),
            patch('marcel_core.tools.web.duckduckgo.DuckDuckGoBackend', side_effect=_ddg_factory),
        ):
            result = await web(_ctx(), action='search', query='query')

        assert 'Search error: Brave API key invalid' in result
        assert ddg_called is False, 'DDG must not be called for 401 errors'

    @pytest.mark.asyncio
    async def test_brave_network_failure_does_not_fail_over(self):
        brave = _brave_mock(side_effect=SearchBackendError('network failure — DNS fail'))

        ddg_called = False

        def _ddg_factory():
            nonlocal ddg_called
            ddg_called = True
            return _ddg_mock(return_value=[])

        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=brave),
            patch('marcel_core.tools.web.duckduckgo.DuckDuckGoBackend', side_effect=_ddg_factory),
        ):
            result = await web(_ctx(), action='search', query='query')

        assert 'Search error: network failure' in result
        assert ddg_called is False

    @pytest.mark.asyncio
    async def test_ddg_primary_429_does_not_fail_over_to_itself(self):
        """If the primary backend is already DDG, a rate-limit-style error
        should surface unchanged — no infinite loop of DDG→DDG.
        """
        ddg_primary = _ddg_mock(side_effect=SearchBackendError('rate limit thing'))

        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=ddg_primary),
        ):
            result = await web(_ctx(), action='search', query='query')

        assert 'Search error: rate limit thing' in result
        ddg_primary.search.assert_awaited_once()


class TestDdgFallbackWarningOnce:
    """The 'no BRAVE_API_KEY' warning should fire once per process, not per call."""

    def test_warning_throttled(self):
        import marcel_core.tools.web.backends as backends_mod

        # Reset the module-level flag for a clean test
        backends_mod._ddg_fallback_warned = False

        with patch('marcel_core.config.settings') as mock_settings:
            mock_settings.brave_api_key = None
            mock_settings.web_search_backend = None

            with patch.object(backends_mod.log, 'warning') as mock_warn:
                backends_mod.select_backend()
                backends_mod.select_backend()
                backends_mod.select_backend()

        assert mock_warn.call_count == 1
