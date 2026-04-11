"""Scenario-based tests for the news integration and SQLite cache.

Covers: news.sync, news.search, news.recent through realistic scraping
and querying workflows, plus cache edge cases.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# Cache layer (direct)
# ---------------------------------------------------------------------------


class TestNewsCache:
    def test_upsert_and_query(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        articles = [
            {
                'title': 'AI Boom',
                'source': 'VRT NWS',
                'link': 'https://vrt.be/1',
                'topic': 'Tech',
                'description': 'AI is booming',
            },
            {
                'title': 'Markets Up',
                'source': 'De Tijd',
                'link': 'https://tijd.be/1',
                'topic': 'Finance',
                'description': 'Markets rally',
            },
        ]
        count = upsert_articles('alice', articles)
        assert count == 2

        all_articles = get_articles('alice')
        assert len(all_articles) == 2

    def test_upsert_deduplication(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        article = [{'title': 'Same', 'source': 'VRT', 'link': 'https://vrt.be/same', 'topic': 'Tech'}]
        upsert_articles('alice', article)
        upsert_articles('alice', article)  # same link

        all_articles = get_articles('alice')
        assert len(all_articles) == 1

    def test_upsert_skips_no_link(self):
        from marcel_core.skills.integrations.news.cache import upsert_articles

        articles = [{'title': 'No Link', 'source': 'VRT'}]
        count = upsert_articles('alice', articles)
        assert count == 0

    def test_upsert_uses_url_field(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        articles = [{'title': 'URL field', 'source': 'VRT', 'url': 'https://vrt.be/url-field'}]
        count = upsert_articles('alice', articles)
        assert count == 1
        all_arts = get_articles('alice')
        assert all_arts[0]['link'] == 'https://vrt.be/url-field'

    def test_filter_by_source(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        upsert_articles(
            'alice',
            [
                {'title': 'A', 'source': 'VRT', 'link': 'https://vrt.be/a'},
                {'title': 'B', 'source': 'Tijd', 'link': 'https://tijd.be/b'},
            ],
        )
        results = get_articles('alice', source='VRT')
        assert len(results) == 1
        assert results[0]['source'] == 'VRT'

    def test_filter_by_topic(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        upsert_articles(
            'alice',
            [
                {'title': 'A', 'source': 'VRT', 'link': 'https://1', 'topic': 'Tech'},
                {'title': 'B', 'source': 'VRT', 'link': 'https://2', 'topic': 'Sports'},
            ],
        )
        results = get_articles('alice', topic='Tech')
        assert len(results) == 1

    def test_keyword_search(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        upsert_articles(
            'alice',
            [
                {
                    'title': 'AI Revolution',
                    'source': 'VRT',
                    'link': 'https://1',
                    'description': 'Artificial intelligence',
                },
                {'title': 'Weather', 'source': 'VRT', 'link': 'https://2', 'description': 'Rain tomorrow'},
            ],
        )
        results = get_articles('alice', search='Revolution')
        assert len(results) == 1
        assert results[0]['title'] == 'AI Revolution'

    def test_date_filters(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        upsert_articles(
            'alice',
            [
                {'title': 'Old', 'source': 'VRT', 'link': 'https://1'},
                {'title': 'New', 'source': 'VRT', 'link': 'https://2'},
            ],
        )
        # Both should appear with no date filter
        results = get_articles('alice')
        assert len(results) == 2

        # With date range
        results = get_articles('alice', date_from='2020-01-01', date_to='2099-12-31')
        assert len(results) == 2

    def test_limit(self):
        from marcel_core.skills.integrations.news.cache import get_articles, upsert_articles

        arts = [{'title': f'Art {i}', 'source': 'VRT', 'link': f'https://vrt.be/{i}'} for i in range(10)]
        upsert_articles('alice', arts)
        results = get_articles('alice', limit=3)
        assert len(results) == 3

    def test_filter_new_links(self):
        from marcel_core.skills.integrations.news.cache import filter_new_links, upsert_articles

        upsert_articles('alice', [{'title': 'Existing', 'source': 'VRT', 'link': 'https://vrt.be/exists'}])
        new = filter_new_links('alice', ['https://vrt.be/exists', 'https://vrt.be/new'])
        assert new == ['https://vrt.be/new']

    def test_filter_new_links_empty(self):
        from marcel_core.skills.integrations.news.cache import filter_new_links

        assert filter_new_links('alice', []) == []

    def test_article_id_stable(self):
        from marcel_core.skills.integrations.news.cache import article_id

        assert article_id('https://vrt.be/1') == article_id('https://vrt.be/1')
        assert article_id('https://vrt.be/1') != article_id('https://vrt.be/2')


# ---------------------------------------------------------------------------
# Integration handlers
# ---------------------------------------------------------------------------


class TestNewsIntegration:
    @pytest.mark.asyncio
    async def test_sync(self):
        from marcel_core.skills.integrations.news import sync as sync_handler

        mock_summary = {'new': 5, 'total_fetched': 100, 'unique': 80, 'sources': []}
        with patch('marcel_core.skills.integrations.news.sync.sync_feeds', new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = mock_summary
            result = await sync_handler({}, 'alice')

        data = json.loads(result)
        assert data['new'] == 5
        mock_sync.assert_awaited_once_with('alice')

    @pytest.mark.asyncio
    async def test_search(self):
        from marcel_core.skills.integrations.news import search
        from marcel_core.skills.integrations.news.cache import upsert_articles

        upsert_articles(
            'alice',
            [{'title': 'AI News', 'source': 'VRT', 'link': 'https://vrt.be/ai', 'topic': 'Tech'}],
        )

        result = await search({'source': 'VRT', 'topic': 'Tech', 'limit': '10'}, 'alice')
        data = json.loads(result)
        assert data['count'] == 1

    @pytest.mark.asyncio
    async def test_recent(self):
        from marcel_core.skills.integrations.news import recent
        from marcel_core.skills.integrations.news.cache import upsert_articles

        upsert_articles(
            'alice',
            [{'title': 'Latest', 'source': 'VRT', 'link': 'https://vrt.be/latest'}],
        )

        result = await recent({'limit': '5'}, 'alice')
        data = json.loads(result)
        assert data['count'] == 1


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


class TestNewsSync:
    @pytest.mark.asyncio
    async def test_sync_feeds_stores_new_articles(self, tmp_path):
        from marcel_core.skills.integrations.news.cache import get_articles
        from marcel_core.skills.integrations.news.sync import sync_feeds

        feed_config = [
            {
                'name': 'Test Source',
                'feeds': ['https://example.com/feed.xml'],
                'exclude_categories': [],
            }
        ]

        mock_articles = [
            {'title': 'Article 1', 'link': 'https://example.com/1', 'category': 'Tech', 'description': 'Desc 1'},
            {'title': 'Article 2', 'link': 'https://example.com/2', 'category': 'Finance', 'description': 'Desc 2'},
        ]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = mock_articles
            result = await sync_feeds('alice')

        assert result['new'] == 2
        assert result['total_fetched'] == 2

        # Verify articles are actually in the database
        stored = get_articles('alice')
        assert len(stored) == 2
        assert stored[0]['source'] == 'Test Source'

    @pytest.mark.asyncio
    async def test_sync_feeds_deduplicates(self, tmp_path):
        from marcel_core.skills.integrations.news.sync import sync_feeds

        feed_config = [
            {
                'name': 'Source A',
                'feeds': ['https://a.com/feed1.xml', 'https://a.com/feed2.xml'],
            }
        ]

        same_article = [
            {'title': 'Same Article', 'link': 'https://a.com/same', 'category': 'Tech'},
        ]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = same_article
            result = await sync_feeds('alice')

        # Same link from two feeds should be stored only once
        assert result['new'] == 1
        assert result['unique'] == 1

    @pytest.mark.asyncio
    async def test_sync_feeds_skips_known_articles(self, tmp_path):
        from marcel_core.skills.integrations.news.cache import upsert_articles
        from marcel_core.skills.integrations.news.sync import sync_feeds

        # Pre-populate the database
        upsert_articles('alice', [{'title': 'Old', 'source': 'VRT', 'link': 'https://vrt.be/old'}])

        feed_config = [{'name': 'VRT', 'feeds': ['https://vrt.be/feed.xml']}]

        articles = [
            {'title': 'Old', 'link': 'https://vrt.be/old'},
            {'title': 'New', 'link': 'https://vrt.be/new'},
        ]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = articles
            result = await sync_feeds('alice')

        assert result['new'] == 1  # only the new one

    @pytest.mark.asyncio
    async def test_sync_feeds_excludes_categories(self, tmp_path):
        from marcel_core.skills.integrations.news.sync import sync_feeds

        feed_config = [
            {
                'name': 'VRT NWS',
                'feeds': ['https://vrt.be/feed.xml'],
                'exclude_categories': ['sport', 'weer'],
            }
        ]

        articles = [
            {'title': 'News', 'link': 'https://vrt.be/news', 'category': 'binnenland'},
            {'title': 'Sport', 'link': 'https://vrt.be/sport', 'category': 'Sport'},
            {'title': 'Weather', 'link': 'https://vrt.be/weer', 'category': 'Weer'},
        ]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = articles
            result = await sync_feeds('alice')

        assert result['new'] == 1  # only 'binnenland' article

    @pytest.mark.asyncio
    async def test_sync_feeds_handles_fetch_error(self, tmp_path):
        from marcel_core.skills.integrations.news.sync import sync_feeds

        feed_config = [
            {
                'name': 'Broken',
                'feeds': ['https://broken.com/feed.xml'],
            },
            {
                'name': 'Working',
                'feeds': ['https://working.com/feed.xml'],
            },
        ]

        async def mock_fetch(url, max_articles=50):
            if 'broken' in url:
                raise ConnectionError('DNS failed')
            return [{'title': 'Works', 'link': 'https://working.com/1', 'category': 'Tech'}]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', side_effect=mock_fetch),
        ):
            result = await sync_feeds('alice')

        # Should still store the working source's articles
        assert result['new'] == 1

    @pytest.mark.asyncio
    async def test_sync_feeds_no_sources(self, tmp_path):
        from marcel_core.skills.integrations.news.sync import sync_feeds

        with patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=[]):
            result = await sync_feeds('alice')

        assert result['new'] == 0
        assert 'error' in result

    def test_load_feed_config_from_defaults(self):
        from marcel_core.skills.integrations.news.sync import load_feed_config

        # Should load from bundled defaults (no user override in tmp_path)
        sources = load_feed_config()
        assert len(sources) > 0
        assert sources[0]['name'] == 'VRT NWS'
        assert len(sources[0]['feeds']) > 0

    @pytest.mark.asyncio
    async def test_sync_feeds_maps_category_to_topic(self, tmp_path):
        from marcel_core.skills.integrations.news.cache import get_articles
        from marcel_core.skills.integrations.news.sync import sync_feeds

        feed_config = [{'name': 'Test', 'feeds': ['https://test.com/feed.xml']}]
        articles = [{'title': 'Art', 'link': 'https://test.com/1', 'category': 'Politiek'}]

        with (
            patch('marcel_core.skills.integrations.news.sync.load_feed_config', return_value=feed_config),
            patch('marcel_core.skills.integrations.news.sync.fetch_feed', new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = articles
            await sync_feeds('alice')

        stored = get_articles('alice')
        assert stored[0]['topic'] == 'Politiek'
