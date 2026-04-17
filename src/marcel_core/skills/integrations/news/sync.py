"""News feed sync — fetch RSS feeds and store new articles in the cache.

Loads feed URLs from ``feeds.yaml`` (user skill dir, falling back to
bundled defaults) and fetches them all concurrently.  New articles are
deduplicated and stored via the news cache.

This replaces the previous approach of having an LLM agent orchestrate
individual ``rss_fetch`` tool calls — all the work now happens in code.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yaml

from marcel_core.skills.integrations.news import cache
from marcel_core.tools.rss import fetch_feed

log = logging.getLogger(__name__)

_DEFAULTS_DIR = (Path(__file__).resolve().parent / '../../../defaults/skills/news').resolve()


# ---------------------------------------------------------------------------
# Feed config loading
# ---------------------------------------------------------------------------


def _feeds_yaml_path(user_slug: str | None = None) -> Path:
    """Resolve the feeds.yaml path: user override > bundled default."""
    if user_slug:
        from marcel_core.config import settings

        user_path = settings.data_dir / 'skills' / 'news' / 'feeds.yaml'
        if user_path.exists():
            return user_path

    # Fall back to bundled defaults
    default_path = _DEFAULTS_DIR / 'feeds.yaml'
    if default_path.exists():
        return default_path

    raise FileNotFoundError('No feeds.yaml found in user skills or bundled defaults')


def load_feed_config(user_slug: str | None = None) -> list[dict[str, Any]]:
    """Load and return the list of source configs from feeds.yaml."""
    path = _feeds_yaml_path(user_slug)
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data.get('sources', [])


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------


async def _fetch_source(
    source_name: str,
    feed_url: str,
    exclude_categories: set[str],
    max_articles: int = 50,
) -> list[dict[str, str]]:
    """Fetch a single feed and annotate articles with source name.

    Returns an empty list on error (logged, not raised).
    """
    try:
        raw_articles = await fetch_feed(feed_url, max_articles=max_articles)
    except ValueError as exc:
        # Non-XML response — the feed is dead or redirecting to HTML. One
        # line is enough, no traceback.
        log.warning('[news-sync] Skipping %s (%s): %s', feed_url, source_name, exc)
        return []
    except Exception:
        log.warning('[news-sync] Failed to fetch %s (%s)', feed_url, source_name, exc_info=True)
        return []

    articles: list[dict[str, str]] = []
    for art in raw_articles:
        # Apply category exclusion
        category = (art.get('category') or '').lower()
        if category and category in exclude_categories:
            continue

        art['source'] = source_name
        # Map RSS 'category' field to 'topic' for storage
        if 'category' in art:
            art['topic'] = art.pop('category')
        articles.append(art)

    return articles


async def sync_feeds(user_slug: str) -> dict[str, Any]:
    """Fetch all configured RSS feeds, deduplicate, and store new articles.

    Returns a summary dict with counts per source and total new articles.
    """
    sources = load_feed_config(user_slug)
    if not sources:
        return {'error': 'No sources configured in feeds.yaml', 'new': 0, 'sources': []}

    # Build fetch tasks for all feeds across all sources
    tasks: list[tuple[str, set[str], asyncio.Task[list[dict[str, str]]]]] = []
    for source in sources:
        name = source['name']
        feeds = source.get('feeds', [])
        excludes = {c.lower() for c in source.get('exclude_categories', [])}
        for url in feeds:
            task = asyncio.create_task(_fetch_source(name, url, excludes))
            tasks.append((name, excludes, task))

    # Gather all results
    all_articles: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    for name, _excludes, task in tasks:
        articles = await task
        all_articles.extend(articles)
        source_counts[name] = source_counts.get(name, 0) + len(articles)

    if not all_articles:
        log.info('[news-sync] No articles fetched from any source for user=%s', user_slug)
        return {'new': 0, 'total_fetched': 0, 'sources': []}

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for art in all_articles:
        link = art.get('link', '')
        if link and link not in seen:
            seen.add(link)
            unique.append(art)

    # Filter out articles already in the database
    all_links = [art['link'] for art in unique if art.get('link')]
    new_links = set(cache.filter_new_links(user_slug, all_links))

    new_articles = [art for art in unique if art.get('link') in new_links]

    # Store new articles
    stored = 0
    if new_articles:
        stored = cache.upsert_articles(user_slug, new_articles)

    # Build per-source summary
    source_summary = [{'name': name, 'fetched': count} for name, count in sorted(source_counts.items())]

    log.info(
        '[news-sync] user=%s fetched=%d unique=%d new=%d stored=%d',
        user_slug,
        len(all_articles),
        len(unique),
        len(new_links),
        stored,
    )

    return {
        'new': stored,
        'total_fetched': len(all_articles),
        'unique': len(unique),
        'sources': source_summary,
    }
