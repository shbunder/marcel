"""Scenario-based tests for tools/rss.py — RSS and Atom feed parsing.

Tests the feed parser through realistic XML inputs (RSS 2.0, Atom, RDF)
without making network calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marcel_core.harness.context import MarcelDeps
from marcel_core.tools.rss import _parse_feed, rss_fetch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RSS_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Article One</title>
      <link>https://example.com/1</link>
      <description>First article description that is long enough to test truncation</description>
      <pubDate>Sat, 10 Apr 2026 10:00:00 GMT</pubDate>
      <category>Tech</category>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/2</link>
      <description>Short desc</description>
    </item>
    <item>
      <link>https://example.com/no-title</link>
      <description>No title item</description>
    </item>
  </channel>
</rss>"""

_ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom Article</title>
    <link rel="alternate" href="https://example.com/atom/1" />
    <summary>Atom summary</summary>
    <published>2026-04-10T10:00:00Z</published>
    <category term="Science" />
  </entry>
  <entry>
    <title>No Alternate Link</title>
    <link rel="related" href="https://example.com/related" />
    <content>Full content here</content>
    <updated>2026-04-10T11:00:00Z</updated>
  </entry>
  <entry>
    <title>ID as URL</title>
    <id>https://example.com/atom/3</id>
    <summary>Uses id as link</summary>
  </entry>
  <entry>
    <link rel="alternate" href="https://example.com/no-title-atom" />
    <summary>No title</summary>
  </entry>
</feed>"""

_RDF_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/">
  <item>
    <title>RDF Article</title>
    <link>https://example.com/rdf/1</link>
    <description>RDF desc</description>
  </item>
</rdf:RDF>"""


def _ctx() -> MagicMock:
    deps = MarcelDeps(user_slug='alice', conversation_id='conv-1', channel='cli')
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# RSS parsing — coverage obtained through feed parsing scenarios
# ---------------------------------------------------------------------------


class TestRSSParsing:
    def test_parses_rss_articles(self):
        articles = _parse_feed(_RSS_FEED)
        assert len(articles) == 2  # item without title is skipped
        assert articles[0]['title'] == 'Article One'
        assert articles[0]['link'] == 'https://example.com/1'
        assert articles[0]['category'] == 'Tech'
        assert 'published' in articles[0]

    def test_description_truncated(self):
        articles = _parse_feed(_RSS_FEED)
        assert len(articles[0].get('description', '')) <= 200


# ---------------------------------------------------------------------------
# Atom parsing
# ---------------------------------------------------------------------------


class TestAtomParsing:
    def test_parses_atom_articles(self):
        articles = _parse_feed(_ATOM_FEED)
        assert len(articles) == 3  # entry without title skipped

    def test_alternate_link_preferred(self):
        articles = _parse_feed(_ATOM_FEED)
        assert articles[0]['link'] == 'https://example.com/atom/1'

    def test_related_link_fallback(self):
        articles = _parse_feed(_ATOM_FEED)
        no_alt = [a for a in articles if a['title'] == 'No Alternate Link']
        assert len(no_alt) == 1
        assert no_alt[0]['link'] == 'https://example.com/related'

    def test_id_as_url_fallback(self):
        articles = _parse_feed(_ATOM_FEED)
        id_art = [a for a in articles if a['title'] == 'ID as URL']
        assert len(id_art) == 1
        assert id_art[0]['link'] == 'https://example.com/atom/3'

    def test_category_term(self):
        articles = _parse_feed(_ATOM_FEED)
        assert articles[0].get('category') == 'Science'


# ---------------------------------------------------------------------------
# RDF / RSS 1.0
# ---------------------------------------------------------------------------


class TestAtomNoNamespace:
    def test_atom_without_namespace(self):
        """Atom feed without xmlns namespace."""
        atom_bare = """\
<?xml version="1.0"?>
<feed>
  <entry>
    <title>Bare Atom</title>
    <link rel="alternate" href="https://example.com/bare" />
    <summary>No namespace</summary>
  </entry>
</feed>"""
        articles = _parse_feed(atom_bare)
        assert len(articles) == 1
        assert articles[0]['title'] == 'Bare Atom'


class TestRDFParsing:
    def test_parses_rdf_via_parse_feed(self):
        """RDF 1.0 uses namespaced <item> so _parse_rss finds them via .iter('item')."""
        # Use a simpler RDF without default namespace so iter('item') matches
        rdf_simple = """\
<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <item>
    <title>RDF Article</title>
    <link>https://example.com/rdf/1</link>
    <description>RDF desc</description>
  </item>
</rdf:RDF>"""
        articles = _parse_feed(rdf_simple)
        assert len(articles) == 1
        assert articles[0]['title'] == 'RDF Article'


# ---------------------------------------------------------------------------
# rss_fetch tool (mocked HTTP)
# ---------------------------------------------------------------------------


class TestRssFetch:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        mock_resp = MagicMock()
        mock_resp.text = _RSS_FEED
        mock_resp.raise_for_status = MagicMock()
        mock_resp.is_success = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch('marcel_core.tools.rss.httpx.AsyncClient', return_value=mock_client):
            result = await rss_fetch(_ctx(), 'https://example.com/feed.xml', max_articles=5)

        import json

        articles = json.loads(result)
        assert len(articles) == 2

    @pytest.mark.asyncio
    async def test_http_error(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        exc = httpx.HTTPStatusError('Not found', request=MagicMock(), response=mock_resp)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=exc)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch('marcel_core.tools.rss.httpx.AsyncClient', return_value=mock_client):
            result = await rss_fetch(_ctx(), 'https://example.com/bad')
        assert 'Error: HTTP 404' in result

    @pytest.mark.asyncio
    async def test_connection_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError('DNS failed'))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch('marcel_core.tools.rss.httpx.AsyncClient', return_value=mock_client):
            result = await rss_fetch(_ctx(), 'https://example.com/fail')
        assert 'Error: Failed to fetch' in result

    @pytest.mark.asyncio
    async def test_malformed_xml(self):
        mock_resp = MagicMock()
        mock_resp.text = 'not valid xml <><>'
        mock_resp.raise_for_status = MagicMock()
        mock_resp.is_success = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch('marcel_core.tools.rss.httpx.AsyncClient', return_value=mock_client):
            result = await rss_fetch(_ctx(), 'https://example.com/broken.xml')
        assert 'Error: Failed to parse' in result

    @pytest.mark.asyncio
    async def test_max_articles_clamped(self):
        # Generate a feed with many articles
        items = '\n'.join(f'<item><title>Art {i}</title><link>https://example.com/{i}</link></item>' for i in range(20))
        feed = f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'

        mock_resp = MagicMock()
        mock_resp.text = feed
        mock_resp.raise_for_status = MagicMock()
        mock_resp.is_success = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch('marcel_core.tools.rss.httpx.AsyncClient', return_value=mock_client):
            import json

            result = await rss_fetch(_ctx(), 'https://example.com/big.xml', max_articles=3)
            articles = json.loads(result)
        assert len(articles) == 3
