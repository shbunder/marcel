"""``web(action="search")`` action implementation.

Enforces the per-turn rate limit, selects a backend, runs the query,
maps errors to ``Search error: ...`` strings, and formats the happy
path via :mod:`marcel_core.tools.web.formatter`.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.web.backends import SearchBackendError, select_backend
from marcel_core.tools.web.formatter import format_results

log = logging.getLogger(__name__)

MAX_SEARCHES_PER_TURN = 5
"""Cap on ``web(action="search")`` calls per turn.

Protects the primary backend's free-tier quota from runaway loops. The
error returned at the cap is intentionally actionable — it tells the
model what to do instead of spinning.
"""

_MIN_RESULTS = 1
_MAX_RESULTS = 20


async def run_search(
    ctx: RunContext[MarcelDeps],
    query: str | None,
    max_results: int,
) -> str:
    """Run a single web search and return formatted text for the model."""
    if not query or not query.strip():
        return 'Search error: query is required'

    if ctx.deps.turn.web_search_count >= MAX_SEARCHES_PER_TURN:
        return (
            f'Search error: per-turn search limit reached ({MAX_SEARCHES_PER_TURN}). '
            'Summarise what you have or ask the user to narrow the query.'
        )
    ctx.deps.turn.web_search_count += 1

    clamped = max(_MIN_RESULTS, min(max_results, _MAX_RESULTS))

    try:
        backend = select_backend()
    except SearchBackendError as exc:
        return f'Search error: {exc.reason}'

    try:
        results = await backend.search(query.strip(), clamped)
    except SearchBackendError as exc:
        return f'Search error: {exc.reason}'
    except Exception as exc:  # noqa: BLE001 — tool boundary must never raise
        log.exception('web.search: unexpected backend failure')
        return f'Search error: unexpected failure — {exc}'

    if not results:
        return f'Search error: no results for "{query.strip()}". Try a broader or rephrased query.'

    return format_results(results, query.strip(), backend.name)
