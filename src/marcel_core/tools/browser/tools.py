"""Browser MCP tools — exposes Playwright browser automation to the agent.

Follows the same pattern as ``skills/tool.py``: defines tools using
``create_sdk_mcp_server`` + ``tool()`` from claude_agent_sdk, then
returns a server config for inclusion in ``ClaudeAgentOptions.mcp_servers``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from marcel_core.tools.browser.manager import _build_aria_selector, build_snapshot, take_screenshot
from marcel_core.tools.browser.security import is_url_allowed

if TYPE_CHECKING:
    from marcel_core.tools.browser.manager import BrowserManager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_NAVIGATE_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'url': {'type': 'string', 'description': 'The URL to navigate to.'},
    },
    'required': ['url'],
}

_SCREENSHOT_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'full_page': {
            'type': 'string',
            'enum': ['true', 'false'],
            'description': 'Capture the full scrollable page instead of just the viewport. Default: false.',
        },
        'selector': {
            'type': 'string',
            'description': 'Optional CSS selector to screenshot a specific element.',
        },
    },
}

_SNAPSHOT_SCHEMA: dict = {
    'type': 'object',
    'properties': {},
}

_CLICK_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'ref': {
            'type': 'string',
            'description': 'Element ref number from a previous browser_snapshot result.',
        },
        'selector': {
            'type': 'string',
            'description': 'CSS selector to click. Use ref when possible.',
        },
        'x': {'type': 'string', 'description': 'X coordinate for click (use with y).'},
        'y': {'type': 'string', 'description': 'Y coordinate for click (use with x).'},
    },
}

_TYPE_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'ref': {
            'type': 'string',
            'description': 'Element ref number from a previous browser_snapshot.',
        },
        'selector': {
            'type': 'string',
            'description': 'CSS selector of the input element.',
        },
        'text': {'type': 'string', 'description': 'Text to type into the element.'},
        'press_enter': {
            'type': 'string',
            'enum': ['true', 'false'],
            'description': 'Press Enter after typing. Default: false.',
        },
    },
    'required': ['text'],
}

_SCROLL_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'direction': {
            'type': 'string',
            'enum': ['up', 'down', 'left', 'right'],
            'description': 'Scroll direction.',
        },
        'amount': {
            'type': 'string',
            'description': 'Scroll amount in pixels. Default: 500.',
        },
    },
    'required': ['direction'],
}

_PRESS_KEY_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'key': {
            'type': 'string',
            'description': 'Key to press (e.g. Enter, Escape, Tab, ArrowDown, Backspace). Uses Playwright key names.',
        },
    },
    'required': ['key'],
}

_TAB_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'action': {
            'type': 'string',
            'enum': ['list', 'new', 'switch', 'close'],
            'description': 'Tab action: list open tabs, new tab, switch to tab by index, close current tab.',
        },
        'url': {'type': 'string', 'description': 'URL to open (for "new" action).'},
        'index': {'type': 'string', 'description': 'Tab index to switch to (for "switch" action). 0-based.'},
    },
    'required': ['action'],
}

_CLOSE_SCHEMA: dict = {
    'type': 'object',
    'properties': {},
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_browser_mcp_server(session_key: str, browser_manager: BrowserManager) -> McpSdkServerConfig:
    """Return an in-process MCP server with browser tools bound to a session.

    Args:
        session_key: Unique key for this session (used to isolate browser contexts).
        browser_manager: The process-wide BrowserManager instance.

    Returns:
        A :class:`McpSdkServerConfig` ready for ``ClaudeAgentOptions.mcp_servers``.
    """
    from marcel_core.config import settings

    allowlist = (
        [p.strip() for p in settings.browser_url_allowlist.split(',') if p.strip()]
        if settings.browser_url_allowlist
        else None
    )
    timeout = settings.browser_timeout * 1000  # convert to ms

    # -- navigate -------------------------------------------------------

    async def _navigate_impl(args: dict) -> dict:
        url: str = args.get('url', '')
        if not url:
            return _error('url is required')

        allowed, reason = is_url_allowed(url, allowlist)
        if not allowed:
            return _error(f'URL blocked: {reason}')

        try:
            page = await browser_manager.get_active_page(session_key)
            await page.goto(url, timeout=timeout, wait_until='domcontentloaded')
            title = await page.title()
            snapshot_text, ref_map = await build_snapshot(page)
            browser_manager.set_ref_map(session_key, ref_map)
            return _text(f'Navigated to: {title}\nURL: {page.url}\n\n{snapshot_text}')
        except Exception as exc:
            return _error(f'Navigation failed: {exc}')

    # -- screenshot -----------------------------------------------------

    async def _screenshot_impl(args: dict) -> dict:
        full_page = args.get('full_page', 'false') == 'true'
        selector = args.get('selector')

        try:
            page = await browser_manager.get_active_page(session_key)
            b64 = await take_screenshot(page, full_page=full_page, selector=selector)
            return {
                'content': [
                    {'type': 'image', 'data': b64, 'mimeType': 'image/png'},
                ],
            }
        except Exception as exc:
            return _error(f'Screenshot failed: {exc}')

    # -- snapshot -------------------------------------------------------

    async def _snapshot_impl(args: dict) -> dict:
        try:
            page = await browser_manager.get_active_page(session_key)
            snapshot_text, ref_map = await build_snapshot(page)
            browser_manager.set_ref_map(session_key, ref_map)
            title = await page.title()
            url = page.url
            return _text(f'Page: {title}\nURL: {url}\n\n{snapshot_text}')
        except Exception as exc:
            return _error(f'Snapshot failed: {exc}')

    # -- click ----------------------------------------------------------

    async def _click_impl(args: dict) -> dict:
        ref = args.get('ref')
        selector = args.get('selector')
        x = args.get('x')
        y = args.get('y')

        try:
            page = await browser_manager.get_active_page(session_key)

            if ref:
                ref_map = browser_manager.get_ref_map(session_key)
                ref_info = ref_map.get(int(ref))
                if not ref_info:
                    return _error(f'Ref {ref} not found. Run browser_snapshot first to get current refs.')
                aria_selector = _build_aria_selector(ref_info)
                if aria_selector:
                    await page.locator(aria_selector).first.click(timeout=timeout)
                else:
                    return _error(f'Could not build selector for ref {ref}')
            elif selector:
                await page.click(selector, timeout=timeout)
            elif x is not None and y is not None:
                await page.mouse.click(float(x), float(y))
            else:
                return _error('Provide ref, selector, or x+y coordinates')

            # Brief wait for page updates
            await page.wait_for_timeout(500)
            return _text('Clicked successfully.')
        except Exception as exc:
            return _error(f'Click failed: {exc}')

    # -- type -----------------------------------------------------------

    async def _type_impl(args: dict) -> dict:
        ref = args.get('ref')
        selector = args.get('selector')
        text: str = args.get('text', '')
        press_enter = args.get('press_enter', 'false') == 'true'

        if not text:
            return _error('text is required')

        try:
            page = await browser_manager.get_active_page(session_key)

            if ref:
                ref_map = browser_manager.get_ref_map(session_key)
                ref_info = ref_map.get(int(ref))
                if not ref_info:
                    return _error(f'Ref {ref} not found. Run browser_snapshot first.')
                aria_selector = _build_aria_selector(ref_info)
                if aria_selector:
                    locator = page.locator(aria_selector).first
                    await locator.click(timeout=timeout)
                    await locator.fill(text, timeout=timeout)
                else:
                    return _error(f'Could not build selector for ref {ref}')
            elif selector:
                await page.click(selector, timeout=timeout)
                await page.fill(selector, text, timeout=timeout)
            else:
                # Type into currently focused element
                await page.keyboard.type(text)

            if press_enter:
                await page.keyboard.press('Enter')

            return _text(f'Typed "{text[:50]}{"..." if len(text) > 50 else ""}" successfully.')
        except Exception as exc:
            return _error(f'Type failed: {exc}')

    # -- scroll ---------------------------------------------------------

    async def _scroll_impl(args: dict) -> dict:
        direction: str = args.get('direction', 'down')
        amount = int(args.get('amount', '500'))

        try:
            page = await browser_manager.get_active_page(session_key)

            dx, dy = 0, 0
            if direction == 'down':
                dy = amount
            elif direction == 'up':
                dy = -amount
            elif direction == 'right':
                dx = amount
            elif direction == 'left':
                dx = -amount

            await page.mouse.wheel(dx, dy)
            await page.wait_for_timeout(300)
            return _text(f'Scrolled {direction} by {amount}px.')
        except Exception as exc:
            return _error(f'Scroll failed: {exc}')

    # -- press_key ------------------------------------------------------

    async def _press_key_impl(args: dict) -> dict:
        key: str = args.get('key', '')
        if not key:
            return _error('key is required')

        try:
            page = await browser_manager.get_active_page(session_key)
            await page.keyboard.press(key)
            await page.wait_for_timeout(300)
            return _text(f'Pressed key: {key}')
        except Exception as exc:
            return _error(f'Key press failed: {exc}')

    # -- tab ------------------------------------------------------------

    async def _tab_impl(args: dict) -> dict:
        action: str = args.get('action', 'list')

        try:
            ctx = await browser_manager.get_or_create_context(session_key)
            pages = ctx.pages

            if action == 'list':
                if not pages:
                    return _text('No tabs open.')
                lines = []
                for i, p in enumerate(pages):
                    marker = ' (active)' if p == pages[-1] else ''
                    lines.append(f'[{i}] {await p.title()} — {p.url}{marker}')
                return _text('\n'.join(lines))

            elif action == 'new':
                url = args.get('url', 'about:blank')
                if url != 'about:blank':
                    allowed, reason = is_url_allowed(url, allowlist)
                    if not allowed:
                        return _error(f'URL blocked: {reason}')
                page = await ctx.new_page()
                if url != 'about:blank':
                    await page.goto(url, timeout=timeout, wait_until='domcontentloaded')
                title = await page.title()
                return _text(f'Opened new tab: {title} — {page.url}')

            elif action == 'switch':
                index = int(args.get('index', '0'))
                if index < 0 or index >= len(pages):
                    return _error(f'Tab index {index} out of range (0-{len(pages) - 1})')
                await pages[index].bring_to_front()
                return _text(f'Switched to tab [{index}]: {await pages[index].title()}')

            elif action == 'close':
                if not pages:
                    return _text('No tabs to close.')
                await pages[-1].close()
                return _text('Closed current tab.')

            else:
                return _error(f'Unknown tab action: {action}')

        except Exception as exc:
            return _error(f'Tab operation failed: {exc}')

    # -- close ----------------------------------------------------------

    async def _close_impl(args: dict) -> dict:
        try:
            await browser_manager.close_context(session_key)
            return _text('Browser session closed.')
        except Exception as exc:
            return _error(f'Close failed: {exc}')

    # -- Register tools -------------------------------------------------

    navigate_tool: SdkMcpTool = tool(
        'browser_navigate',
        'Navigate to a URL. Returns the page title and an accessibility snapshot of the page content. '
        'Use this to open web pages. The snapshot shows interactive elements with ref numbers you can use with browser_click/browser_type.',
        _NAVIGATE_SCHEMA,
    )(_navigate_impl)

    screenshot_tool: SdkMcpTool = tool(
        'browser_screenshot',
        'Take a screenshot of the current page. Returns a PNG image. '
        'Use this for visual verification — to see what the page actually looks like. '
        'For reading page structure and interacting with elements, use browser_snapshot instead.',
        _SCREENSHOT_SCHEMA,
    )(_screenshot_impl)

    snapshot_tool: SdkMcpTool = tool(
        'browser_snapshot',
        'Get the accessibility tree of the current page as structured text with ref numbers. '
        'Each element gets a [ref] number you can use with browser_click and browser_type. '
        'This is the primary way to read and understand page content. Refs are invalidated after navigation or page changes — re-snapshot if needed.',
        _SNAPSHOT_SCHEMA,
    )(_snapshot_impl)

    click_tool: SdkMcpTool = tool(
        'browser_click',
        'Click an element on the page. Target by ref number (from browser_snapshot), CSS selector, or x/y coordinates. '
        'Ref-based clicking is most reliable. After clicking, use browser_snapshot to see the updated page.',
        _CLICK_SCHEMA,
    )(_click_impl)

    type_tool: SdkMcpTool = tool(
        'browser_type',
        'Type text into an input element. Target by ref number (from browser_snapshot) or CSS selector. '
        'If neither is provided, types into the currently focused element. Set press_enter to "true" to submit after typing.',
        _TYPE_SCHEMA,
    )(_type_impl)

    scroll_tool: SdkMcpTool = tool(
        'browser_scroll',
        'Scroll the page in a direction (up, down, left, right). Default amount is 500 pixels.',
        _SCROLL_SCHEMA,
    )(_scroll_impl)

    press_key_tool: SdkMcpTool = tool(
        'browser_press_key',
        'Press a keyboard key. Uses Playwright key names: Enter, Escape, Tab, ArrowDown, ArrowUp, ArrowLeft, ArrowRight, Backspace, Delete, Space, etc.',
        _PRESS_KEY_SCHEMA,
    )(_press_key_impl)

    tab_tool: SdkMcpTool = tool(
        'browser_tab',
        'Manage browser tabs. Actions: "list" shows open tabs with indices, "new" opens a URL in a new tab, "switch" activates a tab by index, "close" closes the current tab.',
        _TAB_SCHEMA,
    )(_tab_impl)

    close_tool: SdkMcpTool = tool(
        'browser_close',
        'Close the browser session entirely. Use this when you are done browsing.',
        _CLOSE_SCHEMA,
    )(_close_impl)

    return create_sdk_mcp_server(
        'marcel-browser',
        tools=[
            navigate_tool,
            screenshot_tool,
            snapshot_tool,
            click_tool,
            type_tool,
            scroll_tool,
            press_key_tool,
            tab_tool,
            close_tool,
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(text: str) -> dict:
    return {'content': [{'type': 'text', 'text': text}]}


def _error(text: str) -> dict:
    return {'content': [{'type': 'text', 'text': text}], 'is_error': True}
