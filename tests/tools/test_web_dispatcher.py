"""Tests for the ``web`` dispatcher routing + rate limit + playwright gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps, TurnState
from marcel_core.tools.web.backends import SearchResult
from marcel_core.tools.web.dispatcher import web
from marcel_core.tools.web.search import MAX_SEARCHES_PER_TURN


def _ctx() -> MagicMock:
    deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
    deps.turn = TurnState()
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------


class TestUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown_action_lists_available(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='bogus')
        assert 'Unknown action' in result
        assert 'search, navigate' in result


# ---------------------------------------------------------------------------
# Playwright-unavailable gate
# ---------------------------------------------------------------------------


class TestPlaywrightGate:
    @pytest.mark.asyncio
    async def test_navigate_blocked_when_playwright_missing(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=False):
            result = await web(_ctx(), action='navigate', url='https://example.com')
        assert 'playwright not installed' in result

    @pytest.mark.asyncio
    async def test_snapshot_blocked_when_playwright_missing(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=False):
            result = await web(_ctx(), action='snapshot')
        assert 'playwright not installed' in result

    @pytest.mark.asyncio
    async def test_search_works_without_playwright(self):
        mock_backend = MagicMock()
        mock_backend.name = 'brave'
        mock_backend.search = AsyncMock(return_value=[SearchResult(title='T', url='https://example.com', snippet='S')])
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=False),
            patch('marcel_core.tools.web.search.select_backend', return_value=mock_backend),
        ):
            result = await web(_ctx(), action='search', query='test')
        assert 'Search results for "test"' in result
        assert 'https://example.com' in result


# ---------------------------------------------------------------------------
# Search routing and rate limit
# ---------------------------------------------------------------------------


class TestSearchAction:
    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        ctx = _ctx()
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(ctx, action='search', query='')
        assert 'Search error: query is required' in result

    @pytest.mark.asyncio
    async def test_whitespace_only_query_returns_error(self):
        ctx = _ctx()
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(ctx, action='search', query='   ')
        assert 'Search error: query is required' in result

    @pytest.mark.asyncio
    async def test_rate_limit_enforced(self):
        ctx = _ctx()
        ctx.deps.turn.web_search_count = MAX_SEARCHES_PER_TURN

        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(ctx, action='search', query='test')

        assert 'per-turn search limit' in result
        # Counter should NOT be incremented past the cap
        assert ctx.deps.turn.web_search_count == MAX_SEARCHES_PER_TURN

    @pytest.mark.asyncio
    async def test_counter_increments_on_success(self):
        ctx = _ctx()
        mock_backend = MagicMock()
        mock_backend.name = 'brave'
        mock_backend.search = AsyncMock(return_value=[SearchResult(title='T', url='https://example.com', snippet='S')])
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=mock_backend),
        ):
            await web(ctx, action='search', query='test')
            await web(ctx, action='search', query='test2')

        assert ctx.deps.turn.web_search_count == 2

    @pytest.mark.asyncio
    async def test_no_results_returns_error(self):
        ctx = _ctx()
        mock_backend = MagicMock()
        mock_backend.name = 'brave'
        mock_backend.search = AsyncMock(return_value=[])
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch('marcel_core.tools.web.search.select_backend', return_value=mock_backend),
        ):
            result = await web(ctx, action='search', query='obscure')

        assert 'Search error: no results for "obscure"' in result


# ---------------------------------------------------------------------------
# Browser action routing (dispatch only; underlying functions mocked)
# ---------------------------------------------------------------------------


class TestBrowserDispatch:
    @pytest.mark.asyncio
    async def test_navigate_routes_to_browser_navigate(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_navigate',
                new_callable=AsyncMock,
                return_value='navigated',
            ) as mock_nav,
        ):
            result = await web(_ctx(), action='navigate', url='https://example.com')

        assert result == 'navigated'
        mock_nav.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigate_without_url_returns_error(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='navigate')
        assert 'navigate requires url' in result

    @pytest.mark.asyncio
    async def test_click_routes_with_ref(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_click',
                new_callable=AsyncMock,
                return_value='clicked',
            ) as mock_click,
        ):
            result = await web(_ctx(), action='click', ref=5)

        assert result == 'clicked'
        mock_click.assert_awaited_once()
        assert mock_click.await_args is not None
        assert mock_click.await_args.kwargs['ref'] == 5

    @pytest.mark.asyncio
    async def test_type_without_text_returns_error(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='type', ref=1)
        assert 'type requires text' in result

    @pytest.mark.asyncio
    async def test_type_with_text(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_type',
                new_callable=AsyncMock,
                return_value='typed',
            ) as mock_type,
        ):
            result = await web(_ctx(), action='type', text='hello', ref=1)

        assert result == 'typed'
        mock_type.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scroll_without_direction_returns_error(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='scroll')
        assert 'scroll requires direction' in result

    @pytest.mark.asyncio
    async def test_scroll_uses_default_amount(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_scroll',
                new_callable=AsyncMock,
                return_value='scrolled',
            ) as mock_scroll,
        ):
            await web(_ctx(), action='scroll', direction='down')

        assert mock_scroll.await_args is not None
        assert mock_scroll.await_args.kwargs['amount'] == 500

    @pytest.mark.asyncio
    async def test_press_key_without_key_returns_error(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='press_key')
        assert 'press_key requires key' in result

    @pytest.mark.asyncio
    async def test_evaluate_without_script_returns_error(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='evaluate')
        assert 'evaluate requires script' in result

    @pytest.mark.asyncio
    async def test_snapshot_routes(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_snapshot',
                new_callable=AsyncMock,
                return_value='snap',
            ),
        ):
            result = await web(_ctx(), action='snapshot')
        assert result == 'snap'

    @pytest.mark.asyncio
    async def test_read_routes(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_read',
                new_callable=AsyncMock,
                return_value='readable markdown',
            ) as mock_read,
        ):
            result = await web(_ctx(), action='read')
        assert result == 'readable markdown'
        mock_read.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_routes(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_close',
                new_callable=AsyncMock,
                return_value='closed',
            ),
        ):
            result = await web(_ctx(), action='close')
        assert result == 'closed'

    @pytest.mark.asyncio
    async def test_content_routes_with_selector(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_content',
                new_callable=AsyncMock,
                return_value='<html/>',
            ) as mock_content,
        ):
            await web(_ctx(), action='content', selector='article')
        assert mock_content.await_args is not None
        assert mock_content.await_args.kwargs['selector'] == 'article'

    @pytest.mark.asyncio
    async def test_screenshot_routes(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_screenshot',
                new_callable=AsyncMock,
                return_value='[screenshot]',
            ) as mock_shot,
        ):
            await web(_ctx(), action='screenshot', full_page=True)
        assert mock_shot.await_args is not None
        assert mock_shot.await_args.kwargs['full_page'] is True

    @pytest.mark.asyncio
    async def test_tab_requires_tab_action(self):
        with patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True):
            result = await web(_ctx(), action='tab')
        assert 'tab requires tab_action' in result

    @pytest.mark.asyncio
    async def test_tab_routes(self):
        with (
            patch('marcel_core.tools.web.dispatcher.browser_is_available', return_value=True),
            patch(
                'marcel_core.tools.web.dispatcher._browser_tab',
                new_callable=AsyncMock,
                return_value='tabs',
            ) as mock_tab,
        ):
            await web(_ctx(), action='tab', tab_action='list')
        assert mock_tab.await_args is not None
        assert mock_tab.await_args.kwargs['action'] == 'list'
