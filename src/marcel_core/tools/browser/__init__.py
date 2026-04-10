"""Browser automation package — Playwright-based web interaction for Marcel.

Provides a process-wide :class:`BrowserManager` singleton and pydantic-ai
tool functions for browser automation (navigate, screenshot, snapshot, click,
type, scroll, press_key, tab, close).

Usage::

    from marcel_core.tools.browser import is_available

    if is_available():
        from marcel_core.tools.browser.pydantic_tools import browser_navigate, ...
"""

from __future__ import annotations

import importlib.util

from marcel_core.tools.browser.manager import BrowserManager

# Module-level singleton — shared across all sessions.
browser_manager = BrowserManager()


def is_available() -> bool:
    """Return True if playwright is installed and browser tools can be used."""
    return importlib.util.find_spec('playwright') is not None


__all__ = ['BrowserManager', 'browser_manager', 'is_available']
