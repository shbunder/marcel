"""News integration — structured article storage and retrieval.

Registers ``news.store``, ``news.search``, and ``news.recent`` as
python integration skills, callable through the ``integration`` tool.

Articles are stored per-user in a SQLite database at
``data/users/{slug}/news.db``.
"""

from __future__ import annotations

import json

from marcel_core.skills.integrations import register
from marcel_core.skills.integrations.news import cache


@register('news.store')
async def store(params: dict, user_slug: str) -> str:
    """Store one or more scraped articles.

    Expects ``articles`` — a list of objects with fields:
    ``title``, ``source``, ``link``, ``topic``, ``description``,
    and optionally ``published_at``.
    """
    articles = params.get('articles', [])
    if not articles:
        return json.dumps({'error': 'articles list is required'})

    count = cache.upsert_articles(user_slug, articles)
    return json.dumps({'stored': count})


@register('news.filter_new')
async def filter_new(params: dict, user_slug: str) -> str:
    """Filter a list of links to only those not already stored.

    Expects ``links`` — a list of URL strings.
    Returns ``{"new_links": [...]}`` containing only unknown links.
    """
    links = params.get('links', [])
    if not links:
        return json.dumps({'new_links': [], 'count': 0})

    new = cache.filter_new_links(user_slug, links)
    return json.dumps({'new_links': new, 'count': len(new)})


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
