"""Browser automation package — Playwright-based web interaction for Marcel.

Provides a process-wide :class:`BrowserManager` singleton and an MCP server
builder that exposes browser tools (navigate, screenshot, snapshot, click,
type, scroll, press_key, tab, close) to the agent.

Usage::

    from marcel_core.tools.browser import browser_manager, build_browser_mcp_server

    # In session creation:
    server = build_browser_mcp_server(session_key, browser_manager)
"""

from __future__ import annotations

import importlib.util

from marcel_core.tools.browser.manager import BrowserManager
from marcel_core.tools.browser.tools import build_browser_mcp_server

# Module-level singleton — shared across all sessions.
browser_manager = BrowserManager()


def is_available() -> bool:
    """Return True if playwright is installed and browser tools can be used."""
    return importlib.util.find_spec('playwright') is not None


__all__ = ['BrowserManager', 'browser_manager', 'build_browser_mcp_server', 'is_available']
