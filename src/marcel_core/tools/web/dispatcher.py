"""The ``web`` tool — one entry point that routes to many actions.

This is the only web-related function advertised to the pydantic-ai
agent. It delegates search to :mod:`marcel_core.tools.web.search` and
browser operations to the existing functions in
:mod:`marcel_core.tools.browser.pydantic_tools`. Mirrors the pattern used
by :mod:`marcel_core.tools.marcel.dispatcher`.

Browser actions require Playwright to be installed. When it is not, the
dispatcher returns ``Browser error: playwright not installed`` so the
agent can still use ``web(action="search")``.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.browser import is_available as browser_is_available
from marcel_core.tools.browser.pydantic_tools import (
    browser_click as _browser_click,
    browser_close as _browser_close,
    browser_content as _browser_content,
    browser_evaluate as _browser_evaluate,
    browser_navigate as _browser_navigate,
    browser_press_key as _browser_press_key,
    browser_read as _browser_read,
    browser_screenshot as _browser_screenshot,
    browser_scroll as _browser_scroll,
    browser_snapshot as _browser_snapshot,
    browser_tab as _browser_tab,
    browser_type as _browser_type,
)
from marcel_core.tools.web.search import run_search

log = logging.getLogger(__name__)

_BROWSER_UNAVAILABLE = (
    'Browser error: playwright not installed. Only the "search" action is available in this environment.'
)

_AVAILABLE_ACTIONS = (
    'search, navigate, snapshot, read, screenshot, click, type, scroll, press_key, tab, evaluate, content, close'
)


async def web(
    ctx: RunContext[MarcelDeps],
    action: str,
    # search
    query: str | None = None,
    max_results: int | None = None,
    # navigate / evaluate / press_key / type
    url: str | None = None,
    script: str | None = None,
    key: str | None = None,
    text: str | None = None,
    press_enter: bool | None = None,
    # click / type / content — targeting
    ref: int | None = None,
    selector: str | None = None,
    x: float | None = None,
    y: float | None = None,
    # scroll
    direction: str | None = None,
    amount: int | None = None,
    # screenshot
    full_page: bool | None = None,
    # tab
    tab_action: str | None = None,
    tab_id: int | None = None,
) -> str:
    """Search, navigate, read, and interact with the web.

    You have three increasingly powerful primitives. Pick the cheapest one
    that does the job.

    1. **search** — first resort for any information-gathering query
       ("what is", "latest on", "who/when/where"). Stateless, no
       JavaScript, fast. Always cite the result URLs in your reply.
    2. **navigate + read / content / evaluate** — read a specific URL you
       already have (typically from search results). Handles JavaScript.
       Reach for ``read`` when the navigate snapshot is sparse (React,
       Next.js, Vue SPAs) — it returns Trafilatura-extracted prose.
       ``navigate`` already auto-appends readable content on sparse pages.
    3. **click / type / scroll / press_key** — interactive flows like
       login or form filling. Stateful browser session.

    Never end a turn on a forward-looking stub ("let me try a different
    approach") without calling a tool. If every option fails, report the
    failure plainly so the user can redirect you.

    Actions:
      search(query, max_results=5)
          Search the web and return ranked results with title, URL,
          snippet. First-resort for information queries. Rate-limited to
          5 calls per turn.

      navigate(url)
          Open a URL and return page title + accessibility snapshot.

      snapshot()
          Re-read the current page's accessibility tree with [ref]
          numbers. Refs invalidate after any page change.

      read()
          Return the current page as readable markdown prose, via
          Trafilatura on the hydrated DOM. Use this when snapshot comes
          back sparse — typical of React/Next.js/Vue SPAs where the
          accessibility tree collapses on unsemantic `<div>` soup.

      screenshot(full_page=False, selector=None)
          Visual PNG. Use only when layout or images matter — more
          expensive in tokens than snapshot.

      click(ref?, selector?, x?, y?)
          Click an element. Prefer ref from snapshot.

      type(text, ref?, selector?, press_enter=False)
          Type text into an input.

      scroll(direction, amount?)
          Scroll up/down/left/right.

      press_key(key)
          Press a key (Enter, Escape, Tab, arrows, etc.).

      tab(tab_action, tab_id?)
          Manage tabs: list, new, switch, close.

      evaluate(script)
          Run JavaScript and return the result. Use when the accessibility
          tree is empty.

      content(selector?)
          Return raw HTML of the page or a specific element.

      close()
          Close the browser session. Call when done with a browsing task
          to free resources.

    Args:
        ctx: Agent context with user and conversation info.
        action: The action to perform (see above).
        query: Search query string.
        max_results: Max search results (1-20, default 5).
        url: Target URL for navigate.
        script: JavaScript to run for evaluate.
        key: Key name for press_key.
        text: Text to type.
        press_enter: If True, press Enter after typing.
        ref: Element ref number from snapshot (for click, type).
        selector: CSS selector (for click, type, content).
        x: Click x-coordinate.
        y: Click y-coordinate.
        direction: Scroll direction (up, down, left, right).
        amount: Scroll distance in pixels.
        full_page: Full-page screenshot flag.
        tab_action: Tab sub-action (list, new, switch, close).
        tab_id: Tab id for tab switch/close.

    Returns:
        Action result as text. Errors are prefixed with ``Search error:``
        or ``Browser error:`` so the model can branch on them.
    """
    if action != 'search' and not browser_is_available():
        return _BROWSER_UNAVAILABLE

    match action:
        case 'search':
            return await run_search(ctx, query, max_results or 5)
        case 'navigate':
            if not url:
                return 'Browser error: navigate requires url'
            return await _browser_navigate(ctx, url)
        case 'snapshot':
            return await _browser_snapshot(ctx)
        case 'read':
            return await _browser_read(ctx)
        case 'screenshot':
            return await _browser_screenshot(ctx, full_page=bool(full_page), selector=selector)
        case 'click':
            return await _browser_click(ctx, ref=ref, selector=selector, x=x, y=y)
        case 'type':
            if text is None:
                return 'Browser error: type requires text'
            return await _browser_type(
                ctx,
                text=text,
                ref=ref,
                selector=selector,
                press_enter=bool(press_enter),
            )
        case 'scroll':
            if not direction:
                return 'Browser error: scroll requires direction (up, down, left, right)'
            return await _browser_scroll(ctx, direction=direction, amount=amount or 500)
        case 'press_key':
            if not key:
                return 'Browser error: press_key requires key'
            return await _browser_press_key(ctx, key=key)
        case 'tab':
            if not tab_action:
                return 'Browser error: tab requires tab_action (list, new, switch, close)'
            return await _browser_tab(ctx, action=tab_action, url=url, index=tab_id)
        case 'evaluate':
            if not script:
                return 'Browser error: evaluate requires script'
            return await _browser_evaluate(ctx, script=script)
        case 'content':
            return await _browser_content(ctx, selector=selector)
        case 'close':
            return await _browser_close(ctx)
        case _:
            return f'Unknown action: {action!r}. Available: {_AVAILABLE_ACTIONS}'
