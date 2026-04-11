"""Scenario-based tests for tools/browser/pydantic_tools.py.

All browser interactions are mocked — no real Playwright dependency needed.
Tests cover navigation, clicks, typing, scrolling, tabs, evaluate, content,
screenshots, snapshots, key presses, and close.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.browser.pydantic_tools import (
    browser_click,
    browser_close,
    browser_content,
    browser_evaluate,
    browser_navigate,
    browser_press_key,
    browser_screenshot,
    browser_scroll,
    browser_snapshot,
    browser_tab,
    browser_type,
)


def _ctx() -> MagicMock:
    deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _mock_manager():
    mgr = MagicMock()
    page = AsyncMock()
    page.title = AsyncMock(return_value='Test Page')
    page.url = 'https://example.com'
    page.content = AsyncMock(return_value='<html><body>Hello</body></html>')
    page.goto = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.evaluate = AsyncMock(return_value={'key': 'value'})
    page.query_selector = AsyncMock(return_value=None)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    page.mouse = AsyncMock()
    page.locator = MagicMock()
    page.locator.return_value.first = AsyncMock()
    page.locator.return_value.first.click = AsyncMock()
    page.locator.return_value.first.fill = AsyncMock()

    mgr.get_active_page = AsyncMock(return_value=page)
    mgr.set_ref_map = MagicMock()
    mgr.get_ref_map = MagicMock(return_value={})
    mgr.get_or_create_context = AsyncMock()
    mgr.close_context = AsyncMock()
    return mgr, page


@pytest.fixture
def mock_browser():
    mgr, page = _mock_manager()
    with (
        patch('marcel_core.tools.browser.pydantic_tools._get_manager', return_value=mgr),
        patch('marcel_core.tools.browser.pydantic_tools._get_allowlist', return_value=None),
        patch('marcel_core.tools.browser.pydantic_tools._get_timeout', return_value=30000),
        patch(
            'marcel_core.tools.browser.pydantic_tools.build_snapshot',
            new_callable=AsyncMock,
            return_value=('Snapshot text', {1: {'role': 'button', 'name': 'Submit'}}),
        ),
        patch(
            'marcel_core.tools.browser.pydantic_tools.take_screenshot',
            new_callable=AsyncMock,
            return_value='base64encodedpng',
        ),
    ):
        yield mgr, page


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class TestBrowserNavigate:
    @pytest.mark.asyncio
    async def test_navigate_success(self, mock_browser):
        result = await browser_navigate(_ctx(), 'https://example.com')
        assert 'Test Page' in result
        assert 'Snapshot text' in result

    @pytest.mark.asyncio
    async def test_navigate_blocked_url(self):
        with (
            patch('marcel_core.tools.browser.pydantic_tools._get_allowlist', return_value=['allowed.com']),
            patch('marcel_core.tools.browser.pydantic_tools.is_url_allowed', return_value=(False, 'not in allowlist')),
        ):
            result = await browser_navigate(_ctx(), 'https://blocked.com')
        assert 'Error' in result
        assert 'blocked' in result

    @pytest.mark.asyncio
    async def test_navigate_failure(self, mock_browser):
        mgr, page = mock_browser
        page.goto = AsyncMock(side_effect=RuntimeError('timeout'))
        result = await browser_navigate(_ctx(), 'https://example.com')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


class TestBrowserScreenshot:
    @pytest.mark.asyncio
    async def test_screenshot(self, mock_browser):
        result = await browser_screenshot(_ctx())
        assert 'screenshot taken' in result

    @pytest.mark.asyncio
    async def test_screenshot_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('no page'))
        result = await browser_screenshot(_ctx())
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestBrowserSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot(self, mock_browser):
        result = await browser_snapshot(_ctx())
        assert 'Test Page' in result
        assert 'Snapshot text' in result

    @pytest.mark.asyncio
    async def test_snapshot_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('no page'))
        result = await browser_snapshot(_ctx())
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Click
# ---------------------------------------------------------------------------


class TestBrowserClick:
    @pytest.mark.asyncio
    async def test_click_by_ref(self, mock_browser):
        mgr, page = mock_browser
        mgr.get_ref_map.return_value = {1: {'role': 'button', 'name': 'Submit'}}
        with patch(
            'marcel_core.tools.browser.pydantic_tools._build_aria_selector', return_value='role=button[name="Submit"]'
        ):
            result = await browser_click(_ctx(), ref=1)
        assert 'Clicked' in result

    @pytest.mark.asyncio
    async def test_click_by_selector(self, mock_browser):
        result = await browser_click(_ctx(), selector='#btn')
        assert 'Clicked' in result

    @pytest.mark.asyncio
    async def test_click_by_coords(self, mock_browser):
        result = await browser_click(_ctx(), x=100, y=200)
        assert 'Clicked' in result

    @pytest.mark.asyncio
    async def test_click_no_target(self, mock_browser):
        result = await browser_click(_ctx())
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_click_ref_not_found(self, mock_browser):
        result = await browser_click(_ctx(), ref=999)
        assert 'Error' in result
        assert 'not found' in result

    @pytest.mark.asyncio
    async def test_click_no_aria_selector(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_ref_map.return_value = {1: {'role': 'generic', 'name': ''}}
        with patch('marcel_core.tools.browser.pydantic_tools._build_aria_selector', return_value=None):
            result = await browser_click(_ctx(), ref=1)
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_click_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_click(_ctx(), selector='#x')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------


class TestBrowserType:
    @pytest.mark.asyncio
    async def test_type_by_ref(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_ref_map.return_value = {1: {'role': 'textbox', 'name': 'Search'}}
        with patch('marcel_core.tools.browser.pydantic_tools._build_aria_selector', return_value='role=textbox'):
            result = await browser_type(_ctx(), text='hello', ref=1)
        assert 'Typed' in result

    @pytest.mark.asyncio
    async def test_type_by_selector(self, mock_browser):
        result = await browser_type(_ctx(), text='hello', selector='#input')
        assert 'Typed' in result

    @pytest.mark.asyncio
    async def test_type_focused_element(self, mock_browser):
        result = await browser_type(_ctx(), text='hello')
        assert 'Typed' in result

    @pytest.mark.asyncio
    async def test_type_with_enter(self, mock_browser):
        result = await browser_type(_ctx(), text='search query', press_enter=True)
        assert 'Typed' in result

    @pytest.mark.asyncio
    async def test_type_ref_not_found(self, mock_browser):
        result = await browser_type(_ctx(), text='x', ref=999)
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_type_no_aria_selector(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_ref_map.return_value = {1: {'role': 'generic', 'name': ''}}
        with patch('marcel_core.tools.browser.pydantic_tools._build_aria_selector', return_value=None):
            result = await browser_type(_ctx(), text='x', ref=1)
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_type_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_type(_ctx(), text='x')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Scroll
# ---------------------------------------------------------------------------


class TestBrowserScroll:
    @pytest.mark.asyncio
    async def test_scroll_directions(self, mock_browser):
        for direction in ('up', 'down', 'left', 'right'):
            result = await browser_scroll(_ctx(), direction=direction)
            assert 'Scrolled' in result

    @pytest.mark.asyncio
    async def test_scroll_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_scroll(_ctx(), direction='down')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Key press
# ---------------------------------------------------------------------------


class TestBrowserPressKey:
    @pytest.mark.asyncio
    async def test_press_key(self, mock_browser):
        result = await browser_press_key(_ctx(), key='Enter')
        assert 'Pressed' in result

    @pytest.mark.asyncio
    async def test_press_key_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_press_key(_ctx(), key='Escape')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------


class TestBrowserTab:
    @pytest.mark.asyncio
    async def test_list_tabs(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = [page]
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='list')
        assert 'Test Page' in result

    @pytest.mark.asyncio
    async def test_list_no_tabs(self, mock_browser):
        mgr, _ = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = []
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='list')
        assert 'No tabs' in result

    @pytest.mark.asyncio
    async def test_new_tab(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.new_page = AsyncMock(return_value=page)
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='new', url='https://example.com')
        assert 'Opened' in result

    @pytest.mark.asyncio
    async def test_new_tab_blank(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.new_page = AsyncMock(return_value=page)
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='new')
        assert 'Opened' in result

    @pytest.mark.asyncio
    async def test_new_tab_blocked_url(self, mock_browser):
        mgr, _ = mock_browser
        ctx_mock = AsyncMock()
        mgr.get_or_create_context.return_value = ctx_mock

        with patch('marcel_core.tools.browser.pydantic_tools.is_url_allowed', return_value=(False, 'blocked')):
            with patch('marcel_core.tools.browser.pydantic_tools._get_allowlist', return_value=['allowed.com']):
                result = await browser_tab(_ctx(), action='new', url='https://blocked.com')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_switch_tab(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = [page]
        page.bring_to_front = AsyncMock()
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='switch', index=0)
        assert 'Switched' in result

    @pytest.mark.asyncio
    async def test_switch_tab_out_of_range(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = [page]
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='switch', index=5)
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_close_tab(self, mock_browser):
        mgr, page = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = [page]
        page.close = AsyncMock()
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='close')
        assert 'Closed' in result

    @pytest.mark.asyncio
    async def test_close_no_tabs(self, mock_browser):
        mgr, _ = mock_browser
        ctx_mock = AsyncMock()
        ctx_mock.pages = []
        mgr.get_or_create_context.return_value = ctx_mock

        result = await browser_tab(_ctx(), action='close')
        assert 'No tabs' in result

    @pytest.mark.asyncio
    async def test_unknown_action(self, mock_browser):
        mgr, _ = mock_browser
        ctx_mock = AsyncMock()
        mgr.get_or_create_context.return_value = ctx_mock
        result = await browser_tab(_ctx(), action='bogus')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_tab_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_or_create_context = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_tab(_ctx(), action='list')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------


class TestBrowserEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_dict(self, mock_browser):
        result = await browser_evaluate(_ctx(), script='document.title')
        assert 'key' in result

    @pytest.mark.asyncio
    async def test_evaluate_primitive(self, mock_browser):
        _, page = mock_browser
        page.evaluate = AsyncMock(return_value=42)
        result = await browser_evaluate(_ctx(), script='1+1')
        assert '42' in result

    @pytest.mark.asyncio
    async def test_evaluate_none(self, mock_browser):
        _, page = mock_browser
        page.evaluate = AsyncMock(return_value=None)
        result = await browser_evaluate(_ctx(), script='void 0')
        assert 'null' in result

    @pytest.mark.asyncio
    async def test_evaluate_truncation(self, mock_browser):
        _, page = mock_browser
        page.evaluate = AsyncMock(return_value='x' * 20000)
        result = await browser_evaluate(_ctx(), script='big')
        assert 'truncated' in result

    @pytest.mark.asyncio
    async def test_evaluate_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_evaluate(_ctx(), script='x')
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class TestBrowserContent:
    @pytest.mark.asyncio
    async def test_full_page_content(self, mock_browser):
        result = await browser_content(_ctx())
        assert 'Hello' in result

    @pytest.mark.asyncio
    async def test_selector_content(self, mock_browser):
        _, page = mock_browser
        el = AsyncMock()
        el.inner_html = AsyncMock(return_value='<div>Found</div>')
        page.query_selector = AsyncMock(return_value=el)
        result = await browser_content(_ctx(), selector='#main')
        assert 'Found' in result

    @pytest.mark.asyncio
    async def test_selector_not_found(self, mock_browser):
        result = await browser_content(_ctx(), selector='#missing')
        assert 'Error' in result

    @pytest.mark.asyncio
    async def test_content_truncation(self, mock_browser):
        _, page = mock_browser
        page.content = AsyncMock(return_value='x' * 20000)
        result = await browser_content(_ctx())
        assert 'truncated' in result

    @pytest.mark.asyncio
    async def test_content_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.get_active_page = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_content(_ctx())
        assert 'Error' in result


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestBrowserClose:
    @pytest.mark.asyncio
    async def test_close(self, mock_browser):
        result = await browser_close(_ctx())
        assert 'closed' in result

    @pytest.mark.asyncio
    async def test_close_failure(self, mock_browser):
        mgr, _ = mock_browser
        mgr.close_context = AsyncMock(side_effect=RuntimeError('crash'))
        result = await browser_close(_ctx())
        assert 'Error' in result
