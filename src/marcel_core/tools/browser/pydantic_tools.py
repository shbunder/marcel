"""Browser tools for pydantic-ai — Playwright web automation as native tools.

Wraps the BrowserManager and snapshot/screenshot helpers as pydantic-ai
tool functions for registration in ``harness/agent.py``.

Each tool receives ``RunContext[MarcelDeps]`` and uses the conversation_id
as the browser session key for context isolation.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.browser.manager import _build_aria_selector, build_snapshot, take_screenshot
from marcel_core.tools.browser.security import is_url_allowed

log = logging.getLogger(__name__)


def _session_key(ctx: RunContext[MarcelDeps]) -> str:
    """Derive a browser session key from the request context."""
    return f'{ctx.deps.user_slug}:{ctx.deps.conversation_id}'


def _get_manager():
    """Lazy import to avoid importing playwright at module level."""
    from marcel_core.tools.browser import browser_manager

    return browser_manager


def _get_allowlist() -> list[str] | None:
    from marcel_core.config import settings

    if settings.browser_url_allowlist:
        return [p.strip() for p in settings.browser_url_allowlist.split(',') if p.strip()]
    return None


def _get_timeout() -> int:
    from marcel_core.config import settings

    return settings.browser_timeout * 1000


async def browser_navigate(ctx: RunContext[MarcelDeps], url: str) -> str:
    """Navigate to a URL and return the page title and accessibility snapshot.

    Use this to open web pages. The snapshot shows interactive elements with
    ref numbers you can use with browser_click/browser_type.
    """
    allowed, reason = is_url_allowed(url, _get_allowlist())
    if not allowed:
        return f'Error: URL blocked — {reason}'

    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        page = await mgr.get_active_page(key)
        await page.goto(url, timeout=_get_timeout(), wait_until='domcontentloaded')
        title = await page.title()
        snapshot_text, ref_map = await build_snapshot(page)
        mgr.set_ref_map(key, ref_map)
        return f'Navigated to: {title}\nURL: {page.url}\n\n{snapshot_text}'
    except Exception as exc:
        return f'Error: Navigation failed — {exc}'


async def browser_screenshot(
    ctx: RunContext[MarcelDeps],
    full_page: bool = False,
    selector: str | None = None,
) -> str:
    """Take a screenshot of the current page. Returns a base64-encoded PNG.

    Use this for visual verification. For reading page structure and interacting
    with elements, use browser_snapshot instead.
    """
    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        page = await mgr.get_active_page(key)
        b64 = await take_screenshot(page, full_page=full_page, selector=selector)
        return f'[screenshot taken — {len(b64)} bytes base64]'
    except Exception as exc:
        return f'Error: Screenshot failed — {exc}'


async def browser_snapshot(ctx: RunContext[MarcelDeps]) -> str:
    """Get the accessibility tree of the current page with ref numbers.

    Each element gets a [ref] number you can use with browser_click and
    browser_type. Refs are invalidated after navigation — re-snapshot if needed.
    """
    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        page = await mgr.get_active_page(key)
        snapshot_text, ref_map = await build_snapshot(page)
        mgr.set_ref_map(key, ref_map)
        title = await page.title()
        url = page.url
        return f'Page: {title}\nURL: {url}\n\n{snapshot_text}'
    except Exception as exc:
        return f'Error: Snapshot failed — {exc}'


async def browser_click(
    ctx: RunContext[MarcelDeps],
    ref: int | None = None,
    selector: str | None = None,
    x: float | None = None,
    y: float | None = None,
) -> str:
    """Click an element on the page.

    Target by ref number (from browser_snapshot), CSS selector, or x/y coordinates.
    Ref-based clicking is most reliable. After clicking, use browser_snapshot
    to see the updated page.
    """
    mgr = _get_manager()
    key = _session_key(ctx)
    timeout = _get_timeout()
    try:
        page = await mgr.get_active_page(key)

        if ref is not None:
            ref_map = mgr.get_ref_map(key)
            ref_info = ref_map.get(ref)
            if not ref_info:
                return f'Error: Ref {ref} not found. Run browser_snapshot first to get current refs.'
            aria_selector = _build_aria_selector(ref_info)
            if aria_selector:
                await page.locator(aria_selector).first.click(timeout=timeout)
            else:
                return f'Error: Could not build selector for ref {ref}'
        elif selector:
            await page.click(selector, timeout=timeout)
        elif x is not None and y is not None:
            await page.mouse.click(x, y)
        else:
            return 'Error: Provide ref, selector, or x+y coordinates'

        await page.wait_for_timeout(500)
        return 'Clicked successfully.'
    except Exception as exc:
        return f'Error: Click failed — {exc}'


async def browser_type(
    ctx: RunContext[MarcelDeps],
    text: str,
    ref: int | None = None,
    selector: str | None = None,
    press_enter: bool = False,
) -> str:
    """Type text into an input element.

    Target by ref number (from browser_snapshot) or CSS selector.
    If neither is provided, types into the currently focused element.
    Set press_enter to true to submit after typing.
    """
    mgr = _get_manager()
    key = _session_key(ctx)
    timeout = _get_timeout()
    try:
        page = await mgr.get_active_page(key)

        if ref is not None:
            ref_map = mgr.get_ref_map(key)
            ref_info = ref_map.get(ref)
            if not ref_info:
                return f'Error: Ref {ref} not found. Run browser_snapshot first.'
            aria_selector = _build_aria_selector(ref_info)
            if aria_selector:
                locator = page.locator(aria_selector).first
                await locator.click(timeout=timeout)
                await locator.fill(text, timeout=timeout)
            else:
                return f'Error: Could not build selector for ref {ref}'
        elif selector:
            await page.click(selector, timeout=timeout)
            await page.fill(selector, text, timeout=timeout)
        else:
            await page.keyboard.type(text)

        if press_enter:
            await page.keyboard.press('Enter')

        preview = text[:50] + '...' if len(text) > 50 else text
        return f'Typed "{preview}" successfully.'
    except Exception as exc:
        return f'Error: Type failed — {exc}'


async def browser_scroll(
    ctx: RunContext[MarcelDeps],
    direction: str,
    amount: int = 500,
) -> str:
    """Scroll the page in a direction (up, down, left, right)."""
    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        page = await mgr.get_active_page(key)
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
        return f'Scrolled {direction} by {amount}px.'
    except Exception as exc:
        return f'Error: Scroll failed — {exc}'


async def browser_press_key(ctx: RunContext[MarcelDeps], key: str) -> str:
    """Press a keyboard key.

    Uses Playwright key names: Enter, Escape, Tab, ArrowDown, ArrowUp,
    ArrowLeft, ArrowRight, Backspace, Delete, Space, etc.
    """
    mgr = _get_manager()
    session_key = _session_key(ctx)
    try:
        page = await mgr.get_active_page(session_key)
        await page.keyboard.press(key)
        await page.wait_for_timeout(300)
        return f'Pressed key: {key}'
    except Exception as exc:
        return f'Error: Key press failed — {exc}'


async def browser_tab(
    ctx: RunContext[MarcelDeps],
    action: str,
    url: str | None = None,
    index: int | None = None,
) -> str:
    """Manage browser tabs.

    Actions: "list" shows open tabs with indices, "new" opens a URL in a new tab,
    "switch" activates a tab by index, "close" closes the current tab.
    """
    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        browser_ctx = await mgr.get_or_create_context(key)
        pages = browser_ctx.pages

        if action == 'list':
            if not pages:
                return 'No tabs open.'
            lines = []
            for i, p in enumerate(pages):
                marker = ' (active)' if p == pages[-1] else ''
                lines.append(f'[{i}] {await p.title()} — {p.url}{marker}')
            return '\n'.join(lines)

        elif action == 'new':
            target_url = url or 'about:blank'
            if target_url != 'about:blank':
                allowed, reason = is_url_allowed(target_url, _get_allowlist())
                if not allowed:
                    return f'Error: URL blocked — {reason}'
            page = await browser_ctx.new_page()
            if target_url != 'about:blank':
                await page.goto(target_url, timeout=_get_timeout(), wait_until='domcontentloaded')
            title = await page.title()
            return f'Opened new tab: {title} — {page.url}'

        elif action == 'switch':
            idx = index or 0
            if idx < 0 or idx >= len(pages):
                return f'Error: Tab index {idx} out of range (0-{len(pages) - 1})'
            await pages[idx].bring_to_front()
            return f'Switched to tab [{idx}]: {await pages[idx].title()}'

        elif action == 'close':
            if not pages:
                return 'No tabs to close.'
            await pages[-1].close()
            return 'Closed current tab.'

        else:
            return f'Error: Unknown tab action: {action}'

    except Exception as exc:
        return f'Error: Tab operation failed — {exc}'


async def browser_close(ctx: RunContext[MarcelDeps]) -> str:
    """Close the browser session entirely. Use this when you are done browsing."""
    mgr = _get_manager()
    key = _session_key(ctx)
    try:
        await mgr.close_context(key)
        return 'Browser session closed.'
    except Exception as exc:
        return f'Error: Close failed — {exc}'
