---
name: news
description: Store and query scraped news articles from Belgian sources (VRT NWS, De Tijd, etc.)
requires: {}
---

Help the user with: $ARGUMENTS

You have access to the `integration` tool to store and query news articles. Articles are scraped by a background job and stored locally in a SQLite database with structured metadata.

## Available commands

### news.recent

Get the most recent scraped articles. Use this for "what's in the news?" style questions.

```
integration(id="news.recent")
integration(id="news.recent", params={"source": "VRT NWS", "limit": "10"})
integration(id="news.recent", params={"topic": "economie"})
```

| Param  | Type   | Default | Description                          |
|--------|--------|---------|--------------------------------------|
| source | string | —       | Filter by source (e.g. "VRT NWS")   |
| topic  | string | —       | Filter by topic/category             |
| limit  | string | 20      | Max articles to return               |

### news.search

Query articles with filters — keyword search, source, topic, date range.

```
integration(id="news.search", params={"search": "klimaat"})
integration(id="news.search", params={"source": "De Tijd", "date_from": "2026-04-01"})
integration(id="news.search", params={"topic": "politiek", "limit": "10"})
```

| Param     | Type   | Default | Description                             |
|-----------|--------|---------|-----------------------------------------|
| source    | string | —       | Filter by source                        |
| topic     | string | —       | Filter by topic/category                |
| date_from | string | —       | Start date (ISO), inclusive             |
| date_to   | string | —       | End date (ISO), inclusive               |
| search    | string | —       | Keyword search in title and description |
| limit     | string | 50      | Max articles to return                  |

Returns a JSON object with `articles` (list) and `count`. Each article has: `title`, `source`, `link`, `topic`, `description`, `published_at`, `scraped_at`.

### news.filter_new

Check which article links are not yet in the database. Use this before storing to avoid re-processing known articles.

```
integration(id="news.filter_new", params={"links": ["https://...", "https://..."]})
```

Returns `{"new_links": [...], "count": N}` — only the links not already stored.

### news.store

Store one or more articles (used by scraping jobs, not typically called directly).

```
integration(id="news.store", params={"articles": [{"title": "...", "source": "VRT NWS", "link": "https://...", "topic": "binnenland", "description": "..."}]})
```

Each article object requires `title`, `source`, and `link`. Optional fields: `topic`, `description`, `published_at`.

## Notes

- Articles are deduplicated by URL — re-scraping the same article updates it in place.
- The news scraping job runs at 6am and 6pm and covers: VRT NWS, De Tijd, Knack, Trends, Datanews, De Morgen, and HLN.
- Use `news.filter_new` before storing to skip articles already in the database.
- Topics/categories are assigned by the scraping job based on the source's own categorization.
