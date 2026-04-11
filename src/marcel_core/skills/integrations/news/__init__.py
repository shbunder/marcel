"""News integration — article sync, storage, and retrieval.

Registers ``news.sync``, ``news.search``, and ``news.recent`` as
python integration skills, callable through the ``integration`` tool.

Articles are stored per-user in a SQLite database at
``data/users/{slug}/cache/news.db``.
"""

from __future__ import annotations

import json

from marcel_core.skills.integrations import register
from marcel_core.skills.integrations.news import cache


@register('news.sync')
async def sync(params: dict, user_slug: str) -> str:
    """Fetch all configured RSS feeds, deduplicate, and store new articles.

    No parameters required — feed URLs are loaded from feeds.yaml.
    Returns a summary with counts per source and total new articles.
    """
    from marcel_core.skills.integrations.news.sync import sync_feeds

    summary = await sync_feeds(user_slug)
    return json.dumps(summary, indent=2)


@register('news.search')
async def search(params: dict, user_slug: str) -> str:
    """Query stored articles with optional filters.

    All parameters are optional:
    - ``source``: filter by news source (e.g. "VRT NWS", "De Tijd")
    - ``topic``: filter by topic/category
    - ``date_from`` / ``date_to``: ISO date range
    - ``search``: keyword search in title and description
    - ``limit``: max results (default 50)
    """
    limit = int(params.get('limit', '50'))
    rows = cache.get_articles(
        user_slug,
        source=params.get('source'),
        topic=params.get('topic'),
        date_from=params.get('date_from'),
        date_to=params.get('date_to'),
        search=params.get('search'),
        limit=limit,
    )
    return json.dumps({'articles': rows, 'count': len(rows)}, indent=2)


@register('news.recent')
async def recent(params: dict, user_slug: str) -> str:
    """Get the most recent articles, optionally filtered by source or topic."""
    limit = int(params.get('limit', '20'))
    rows = cache.get_articles(
        user_slug,
        source=params.get('source'),
        topic=params.get('topic'),
        limit=limit,
    )
    return json.dumps({'articles': rows, 'count': len(rows)}, indent=2)
