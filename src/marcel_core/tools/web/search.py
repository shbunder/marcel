"""``web(action="search")`` action implementation.

Enforces the per-turn rate limit, selects a backend, runs the query,
maps errors to ``Search error: ...`` strings, and formats the happy
path via :mod:`marcel_core.tools.web.formatter`.

If the primary backend (Brave) returns a rate-limit error, this module
transparently retries the query against DuckDuckGo so the user gets a
degraded-but-working result instead of a hard failure. All other Brave
errors (401 invalid key, 5xx, network) surface to the model as
``Search error: ...`` strings — silent failover only covers the
quota-exhaustion case, which is the one the caller has no control over.
"""

from __future__ import annotations

import logging
import time

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.web.backends import SearchBackend, SearchBackendError, select_backend
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
        log.info('web.search: per-turn rate limit reached (%d)', MAX_SEARCHES_PER_TURN)
        return (
            f'Search error: per-turn search limit reached ({MAX_SEARCHES_PER_TURN}). '
            'Summarise what you have or ask the user to narrow the query.'
        )
    ctx.deps.turn.web_search_count += 1

    clamped = max(_MIN_RESULTS, min(max_results, _MAX_RESULTS))
    clean_query = query.strip()

    try:
        backend = select_backend()
    except SearchBackendError as exc:
        log.warning('web.search: backend selection failed: %s', exc.reason)
        return f'Search error: {exc.reason}'

    started = time.monotonic()
    results, backend, error = await _run_with_failover(backend, clean_query, clamped)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if error is not None:
        log.warning(
            'web.search: backend=%s query=%r error=%r in %dms',
            backend.name,
            clean_query[:60],
            error,
            elapsed_ms,
        )
        return f'Search error: {error}'

    if not results:
        log.info(
            'web.search: backend=%s query=%r → 0 results in %dms',
            backend.name,
            clean_query[:60],
            elapsed_ms,
        )
        return f'Search error: no results for "{clean_query}". Try a broader or rephrased query.'

    log.info(
        'web.search: backend=%s query=%r → %d results in %dms',
        backend.name,
        clean_query[:60],
        len(results),
        elapsed_ms,
    )
    return format_results(results, clean_query, backend.name)


async def _run_with_failover(
    backend: SearchBackend,
    query: str,
    max_results: int,
) -> tuple[list, SearchBackend, str | None]:
    """Run the search with transparent DuckDuckGo failover on Brave rate-limit.

    Returns ``(results, backend_that_answered, error_or_none)``. A
    rate-limit error from Brave transparently retries against DuckDuckGo;
    the returned ``backend`` is whichever one ultimately answered so the
    caller can log and format with the right name. Any other error (401,
    5xx, network) is returned immediately — silent failover covers the
    quota-exhaustion case only.
    """
    try:
        results = await backend.search(query, max_results)
        return results, backend, None
    except SearchBackendError as exc:
        if backend.name == 'brave' and 'rate limit' in exc.reason.lower():
            log.warning('web.search: Brave rate limit hit, failing over to DuckDuckGo')
            # Local import to avoid pulling httpx into the module load path
            # for callers that only care about the protocol.
            from marcel_core.tools.web.duckduckgo import DuckDuckGoBackend

            ddg = DuckDuckGoBackend()
            try:
                results = await ddg.search(query, max_results)
                return results, ddg, None
            except SearchBackendError as ddg_exc:
                return [], ddg, f'Brave rate limit; DuckDuckGo fallback failed — {ddg_exc.reason}'
        return [], backend, exc.reason
    except Exception as exc:  # noqa: BLE001 — tool boundary must never raise
        log.exception('web.search: unexpected backend failure')
        return [], backend, f'unexpected failure — {exc}'
