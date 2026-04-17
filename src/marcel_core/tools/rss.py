"""RSS feed fetcher — fetch and parse RSS/Atom feeds into structured data.

Provides ``fetch_feed()`` for programmatic use (returns a list of dicts)
and ``rss_fetch()`` as a pydantic-ai agent tool (returns JSON string).
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET

import httpx
from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps

log = logging.getLogger(__name__)

# Realistic browser UA to avoid bot detection on feed endpoints
_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

_MAX_ARTICLES = 100
_TIMEOUT = 30


def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix from a tag name."""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _text(el: ET.Element | None) -> str:
    """Extract text content from an element, or empty string."""
    if el is None:
        return ''
    return (el.text or '').strip()


def _parse_rss(root: ET.Element) -> list[dict[str, str]]:
    """Parse RSS 2.0 feed."""
    articles: list[dict[str, str]] = []
    for item in root.iter('item'):
        if len(articles) >= _MAX_ARTICLES:
            break
        article: dict[str, str] = {}
        for child in item:
            tag = _strip_ns(child.tag).lower()
            text = (child.text or '').strip()
            if tag == 'title':
                article['title'] = text
            elif tag == 'link':
                article['link'] = text
            elif tag == 'description':
                article['description'] = text[:200]
            elif tag in ('pubdate', 'date'):
                article['published'] = text
            elif tag == 'category':
                article.setdefault('category', text)
        if article.get('title'):
            articles.append(article)
    return articles


def _parse_atom(root: ET.Element) -> list[dict[str, str]]:
    """Parse Atom feed."""
    articles: list[dict[str, str]] = []
    ns = ''
    if '}' in root.tag:
        ns = root.tag.split('}')[0] + '}'

    for entry in root.iter(f'{ns}entry'):
        if len(articles) >= _MAX_ARTICLES:
            break
        article: dict[str, str] = {}
        links: list[tuple[str, str]] = []
        for child in entry:
            tag = _strip_ns(child.tag).lower()
            if tag == 'title':
                article['title'] = (child.text or '').strip()
            elif tag == 'link':
                rel = child.get('rel', 'alternate')
                href = child.get('href', '')
                links.append((rel, href))
            elif tag == 'id':
                # Atom <id> is often the canonical article URL
                article.setdefault('id', (child.text or '').strip())
            elif tag in ('summary', 'content'):
                article.setdefault('description', (child.text or '').strip()[:200])
            elif tag in ('published', 'updated'):
                article.setdefault('published', (child.text or '').strip())
            elif tag == 'category':
                article.setdefault('category', child.get('term', ''))
            elif tag in ('nstag',):
                # VRT custom category tag (vrtns:nstag)
                text = (child.text or '').strip()
                if text:
                    article.setdefault('category', text)
        # Pick best link: prefer rel=alternate, then first non-enclosure
        link = ''
        for rel, href in links:
            if rel == 'alternate':
                link = href
                break
        if not link:
            for rel, href in links:
                if rel != 'enclosure':
                    link = href
                    break
        # Fall back to Atom <id> if it looks like a URL
        if not link and article.get('id', '').startswith('http'):
            link = article.pop('id')
        article['link'] = link
        article.pop('id', None)
        if article.get('title'):
            articles.append(article)
    return articles


def _parse_feed(xml_text: str) -> list[dict[str, str]]:
    """Auto-detect RSS vs Atom and parse.

    Feed hosts that have dropped their RSS support typically respond with
    an HTML redirect / marketing page rather than a 404. ``ET.fromstring``
    on that payload raises ``ParseError`` with a traceback — callers don't
    need the stack, they need to know the feed is dead. Sniff the first
    non-whitespace bytes and raise a short :class:`ValueError` instead so
    the caller can log a single-line warning.
    """
    head = xml_text.lstrip()[:200].lower()
    if not head:
        raise ValueError('feed returned an empty body')
    if not (head.startswith('<?xml') or head.startswith('<rss') or head.startswith('<feed') or head.startswith('<rdf')):
        preview = head[:60].replace('\n', ' ')
        raise ValueError(f'feed did not return XML (starts with: {preview!r})')

    root = ET.fromstring(xml_text)
    root_tag = _strip_ns(root.tag).lower()

    if root_tag == 'rss':
        return _parse_rss(root)
    elif root_tag == 'feed':
        return _parse_atom(root)
    elif root_tag == 'rdf':
        # RSS 1.0 / RDF — items are at top level
        return _parse_rss(root)
    else:
        return _parse_rss(root)


async def fetch_feed(url: str, max_articles: int = 50) -> list[dict[str, str]]:
    """Fetch and parse an RSS/Atom feed, returning structured articles.

    This is the programmatic entry point — used by ``news.sync`` and other
    code that needs parsed feed data without going through the agent tool.

    Raises on network or parse errors (callers should handle exceptions).
    """
    async with httpx.AsyncClient(
        headers={'User-Agent': _USER_AGENT},
        timeout=_TIMEOUT,
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    articles = _parse_feed(resp.text)
    clamped = min(max_articles, _MAX_ARTICLES)
    return articles[:clamped]


async def rss_fetch(ctx: RunContext[MarcelDeps], url: str, max_articles: int = 10) -> str:
    """Fetch and parse an RSS or Atom feed URL. Returns structured JSON articles.

    Each article has: title, link, description, published, category.
    Much faster and cheaper than browser-based scraping for sites with feeds.
    """
    try:
        articles = await fetch_feed(url, max_articles)
    except httpx.HTTPStatusError as exc:
        return f'Error: HTTP {exc.response.status_code} fetching {url}'
    except (ET.ParseError, ValueError) as exc:
        return f'Error: Failed to parse feed XML — {exc}'
    except Exception as exc:
        return f'Error: Failed to fetch feed — {exc}'

    return json.dumps(articles, ensure_ascii=False, indent=None, separators=(',', ':'))
