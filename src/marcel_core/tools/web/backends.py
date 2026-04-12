"""Search backend protocol and selection.

The ``web(action="search")`` dispatcher delegates to a backend that
implements :class:`SearchBackend`. Today two backends exist:

- :class:`~marcel_core.tools.web.brave.BraveBackend` — primary, JSON API,
  stable contract, free tier 1000/month
- :class:`~marcel_core.tools.web.duckduckgo.DuckDuckGoBackend` — fallback,
  HTML scraping, no API key, best-effort

Selection is automatic: Brave is used if ``BRAVE_API_KEY`` is set,
otherwise DuckDuckGo with a warning. The ``WEB_SEARCH_BACKEND`` env var is
a safety valve for testing — not documented in the tool docstring.

Adding a new backend: create a new module under
``src/marcel_core/tools/web/``, implement :class:`SearchBackend`, and add
a branch to :func:`select_backend`. If the new backend takes a
user-configurable URL (e.g. a self-hosted SearXNG), call
``marcel_core.tools.browser.security.is_url_allowed`` on it before any
HTTP request — same SSRF protection pattern the browser tool uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# Set to True once the "no BRAVE_API_KEY, falling back to DDG" warning
# has been emitted. The warning is a one-shot operator reminder, not a
# per-call status line — keep docker logs clean when running without a
# Brave key.
_ddg_fallback_warned = False


@dataclass
class SearchResult:
    """A single search result returned by a backend."""

    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    """Search backend contract.

    Implementations must be stateless enough that a single instance can
    handle concurrent calls from multiple turns.
    """

    name: str

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Run a search query and return up to ``max_results`` results.

        Raises :class:`SearchBackendError` on backend failures with a
        short, user-readable reason. The dispatcher converts those into
        ``Search error: <reason>`` strings.
        """
        ...


class SearchBackendError(Exception):
    """Raised by backends when a search cannot be completed.

    The ``reason`` should be a short, lowercase-starting human-readable
    string suitable for embedding in the
    ``Search error: <reason>`` template that the dispatcher returns to
    the model.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def select_backend() -> SearchBackend:
    """Return the active search backend based on configuration.

    Selection logic:

    1. If ``WEB_SEARCH_BACKEND`` env var is set to ``brave`` or
       ``duckduckgo``, use that explicitly (testing safety valve).
    2. Else if ``BRAVE_API_KEY`` is set, use :class:`BraveBackend`.
    3. Else fall back to :class:`DuckDuckGoBackend` with a warning log
       so the operator knows reliability is degraded.
    """
    # Local import so modules can import from backends without pulling in
    # httpx transitively at import time — keeps unit tests light.
    from marcel_core.config import settings
    from marcel_core.tools.web.brave import BraveBackend
    from marcel_core.tools.web.duckduckgo import DuckDuckGoBackend

    override = (settings.web_search_backend or '').strip().lower() or None

    if override == 'brave':
        if not settings.brave_api_key:
            raise SearchBackendError('WEB_SEARCH_BACKEND=brave requires BRAVE_API_KEY to be set')
        return BraveBackend(api_key=settings.brave_api_key)
    if override == 'duckduckgo':
        return DuckDuckGoBackend()
    if override is not None:
        raise SearchBackendError(f'unknown WEB_SEARCH_BACKEND: {override!r}')

    if settings.brave_api_key:
        return BraveBackend(api_key=settings.brave_api_key)

    global _ddg_fallback_warned
    if not _ddg_fallback_warned:
        log.warning(
            'web.search: no BRAVE_API_KEY set, falling back to DuckDuckGo HTML '
            'scraping (unreliable). Get a free key at https://brave.com/search/api/'
        )
        _ddg_fallback_warned = True
    return DuckDuckGoBackend()
