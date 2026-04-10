"""BrowserManager — process-wide Playwright browser with per-session contexts.

Manages a single headless Chromium instance shared across all sessions.
Each conversation session gets its own ``BrowserContext`` with isolated
cookies, storage, and tab state.  Contexts are created lazily and cleaned
up when the session disconnects or goes idle.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright  # pyright: ignore[reportMissingImports]

log = logging.getLogger(__name__)

# Maximum snapshot text length to avoid huge token costs
MAX_SNAPSHOT_CHARS = 8000

# Default viewport for screenshots
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800


class BrowserManager:
    """Process-wide Playwright browser + per-session browser contexts."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._ref_maps: dict[str, dict[int, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        """Launch Playwright and Chromium if not already running."""
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        async with self._lock:
            # Double-check after acquiring lock
            if self._browser is not None and self._browser.is_connected():
                return self._browser

            from playwright.async_api import async_playwright  # pyright: ignore[reportMissingImports]

            from marcel_core.config import settings

            log.info('Launching Playwright Chromium (headless=%s)', settings.browser_headless)
            pw = await async_playwright().start()
            self._playwright = pw
            self._browser = await pw.chromium.launch(
                headless=settings.browser_headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ],
            )
            return self._browser

    async def get_or_create_context(self, session_key: str) -> BrowserContext:
        """Return an isolated browser context for the given session, creating if needed."""
        ctx = self._contexts.get(session_key)
        if ctx is not None:
            return ctx

        browser = await self._ensure_browser()
        ctx = await browser.new_context(
            viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
            user_agent=(
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
        )
        self._contexts[session_key] = ctx
        self._ref_maps[session_key] = {}
        log.info('Created browser context for session %s', session_key)
        return ctx

    async def get_active_page(self, session_key: str) -> Page:
        """Return the active page for a session, creating one if needed."""
        ctx = await self.get_or_create_context(session_key)
        pages = ctx.pages
        if pages:
            return pages[-1]
        page = await ctx.new_page()
        return page

    def get_ref_map(self, session_key: str) -> dict[int, dict[str, Any]]:
        """Return the current ref-to-element mapping for the session."""
        return self._ref_maps.get(session_key, {})

    def set_ref_map(self, session_key: str, ref_map: dict[int, dict[str, Any]]) -> None:
        """Update the ref-to-element mapping for the session."""
        self._ref_maps[session_key] = ref_map

    async def close_context(self, session_key: str) -> None:
        """Close and remove the browser context for a session."""
        self._ref_maps.pop(session_key, None)
        ctx = self._contexts.pop(session_key, None)
        if ctx is not None:
            try:
                await ctx.close()
                log.info('Closed browser context for session %s', session_key)
            except Exception:
                log.exception('Error closing browser context for session %s', session_key)

    async def shutdown(self) -> None:
        """Close all contexts, the browser, and stop Playwright."""
        for key in list(self._contexts):
            await self.close_context(key)

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                log.exception('Error closing browser')
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                log.exception('Error stopping Playwright')
            self._playwright = None

        log.info('BrowserManager shutdown complete')

    @property
    def has_active_contexts(self) -> bool:
        return len(self._contexts) > 0


async def take_screenshot(page: Page, full_page: bool = False, selector: str | None = None) -> str:
    """Take a screenshot and return it as a base64-encoded PNG.

    Resizes to fit within VIEWPORT_WIDTH x VIEWPORT_HEIGHT if needed.
    """
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            raise ValueError(f'Element not found: {selector}')
        screenshot_bytes = await element.screenshot(type='png')
    else:
        screenshot_bytes = await page.screenshot(type='png', full_page=full_page)

    # Resize if too large using PIL if available, otherwise return as-is
    try:
        from PIL import Image

        img = Image.open(BytesIO(screenshot_bytes))
        if img.width > VIEWPORT_WIDTH or img.height > VIEWPORT_HEIGHT:
            img.thumbnail((VIEWPORT_WIDTH, VIEWPORT_HEIGHT), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format='PNG')
            screenshot_bytes = buf.getvalue()
    except ImportError:
        pass  # PIL not available, return original size

    return base64.b64encode(screenshot_bytes).decode('ascii')


async def build_snapshot(page: Page) -> tuple[str, dict[int, dict[str, Any]]]:
    """Build an accessibility-tree snapshot of the page.

    Returns:
        A (snapshot_text, ref_map) tuple. The snapshot text is a compact
        representation with integer refs, and ref_map maps each ref to
        a dict with ``role``, ``name``, and a ``selector`` for targeting.
    """
    try:
        snapshot = await page.accessibility.snapshot()
    except Exception:
        return '(Could not read page accessibility tree)', {}

    if snapshot is None:
        return '(Empty page)', {}

    lines: list[str] = []
    ref_map: dict[int, dict[str, Any]] = {}
    ref_counter = [0]

    def _walk(node: dict, depth: int = 0) -> None:
        role = node.get('role', '')
        name = node.get('name', '')

        # Skip generic/none roles without names
        if role in ('none', 'generic', '') and not name:
            for child in node.get('children', []):
                _walk(child, depth)
            return

        ref_counter[0] += 1
        ref = ref_counter[0]

        # Build display line
        indent = '  ' * depth
        parts = [f'{indent}[{ref}] {role}']
        if name:
            display_name = name[:80] + '...' if len(name) > 80 else name
            parts.append(f'"{display_name}"')

        # Add useful attributes
        if node.get('focused'):
            parts.append('focused')
        if node.get('checked') is not None:
            parts.append('checked' if node['checked'] else 'unchecked')
        if node.get('value'):
            val = str(node['value'])
            val = val[:40] + '...' if len(val) > 40 else val
            parts.append(f'value="{val}"')

        lines.append(' '.join(parts))

        # Store ref mapping for later click/type targeting
        ref_map[ref] = {
            'role': role,
            'name': name,
        }

        for child in node.get('children', []):
            _walk(child, depth + 1)

    _walk(snapshot)

    text = '\n'.join(lines)
    if len(text) > MAX_SNAPSHOT_CHARS:
        text = text[:MAX_SNAPSHOT_CHARS] + '\n\n... (truncated — use a selector to target specific elements)'

    return text, ref_map


def _build_aria_selector(ref_info: dict[str, Any]) -> str:
    """Build an ARIA selector string from ref info for Playwright targeting."""
    role = ref_info.get('role', '')
    name = ref_info.get('name', '')
    if role and name:
        # Escape quotes in name
        escaped = name.replace('"', '\\"')
        return f'role={role}[name="{escaped}"]'
    if role:
        return f'role={role}'
    return ''
