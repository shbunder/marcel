# News Integration

Marcel can pull news articles from RSS feeds, store them locally, and answer questions like "what's in the news?" or "any updates on the climate bill?". Fetching is done in **code**, not by the LLM — feeds are pulled concurrently, deduplicated, and written to a per-user SQLite cache. The agent queries the cache through three `integration()` calls.

Out of the box, Marcel ships with feeds for Belgian news sources (VRT NWS, De Tijd, Knack, Trends, Datanews, De Morgen, HLN). Feeds are configured in a YAML file — add, remove, or swap sources without touching code.

## How it works

```
RSS feeds (YAML config)
        │
        ▼
  news.sync  ── concurrent fetch → dedupe → SQLite cache
        │
        ▼
  news.recent / news.search  ── agent queries the cache
```

The sync step is deterministic: fetch each feed, parse entries, filter by excluded categories, deduplicate by URL, and upsert new articles. The agent never has to orchestrate individual HTTP calls — that's why it's fast, predictable, and testable.

## Setup

The news integration is enabled by default for all users. Articles are cached per-user at `~/.marcel/users/{slug}/cache/news.db`.

No credentials are required — RSS feeds are public.

### Configuring feeds

Feed URLs are loaded from `feeds.yaml`. Marcel looks in two locations in order:

1. `~/.marcel/skills/news/feeds.yaml` (user override)
2. `src/marcel_core/defaults/skills/news/feeds.yaml` (bundled default)

Each source entry has a name, a list of feed URLs, and an optional list of categories to exclude:

```yaml
sources:
  - name: VRT NWS
    feeds:
      - https://www.vrt.be/vrtnws/nl.rss.articles.xml
    exclude_categories:
      - sport
  - name: De Tijd
    feeds:
      - https://www.tijd.be/rss/markten_live.xml
      - https://www.tijd.be/rss/ondernemen.xml
```

To customize per-user, copy the default file to `~/.marcel/skills/news/feeds.yaml` and edit it. Adding or removing sources takes effect on the next `news.sync` call.

### Scheduling sync

The news sync is triggered by a background **job**, not a built-in timer. Create a cron job pointing at the `news.sync` action. The default Marcel setup ships with a 6 AM and 6 PM sync job. To create your own:

```python
integration(id="jobs.create", params={
    "name": "news-sync",
    "trigger": {"type": "cron", "cron": "0 6,18 * * *", "timezone": "Europe/Brussels"},
    "task": "Call integration(id='news.sync') and report the new article count.",
})
```

Running sync manually from a conversation works too — just ask Marcel to "sync the news".

## Skill handlers

All handlers are in `src/marcel_core/skills/integrations/news/` and registered with `@register`:

| Skill | Description |
|---|---|
| `news.sync` | Fetch all configured feeds, deduplicate, and upsert new articles |
| `news.recent` | Return the most recent articles, optionally filtered by source or topic |
| `news.search` | Query articles with filters (source, topic, date range, keyword) |

### `news.sync`

No parameters. Loads `feeds.yaml`, fetches all feeds concurrently, filters out articles matching excluded categories, deduplicates by URL, and stores any links not already in the database. Returns a JSON summary:

```json
{
  "new": 12,
  "total_fetched": 180,
  "unique": 172,
  "sources": [
    {"name": "VRT NWS", "fetched": 40},
    {"name": "De Tijd", "fetched": 85}
  ]
}
```

### `news.recent`

Get the most recent articles. Use this for "what's in the news?" style questions.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | — | Filter by source name (e.g. `"VRT NWS"`) |
| `topic` | string | — | Filter by topic/category |
| `limit` | string | `"20"` | Max articles to return |

```python
integration(id="news.recent")
integration(id="news.recent", params={"source": "VRT NWS", "limit": "10"})
integration(id="news.recent", params={"topic": "economie"})
```

### `news.search`

Query articles with filters. All parameters optional.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | — | Filter by source |
| `topic` | string | — | Filter by topic/category |
| `date_from` | string | — | Start date (ISO), inclusive |
| `date_to` | string | — | End date (ISO), inclusive |
| `search` | string | — | Keyword search across title and description |
| `limit` | string | `"50"` | Max articles to return |

```python
integration(id="news.search", params={"search": "klimaat"})
integration(id="news.search", params={"source": "De Tijd", "date_from": "2026-04-01"})
```

Both `recent` and `search` return a JSON object:

```json
{
  "articles": [
    {
      "title": "...",
      "source": "VRT NWS",
      "link": "https://...",
      "topic": "economie",
      "description": "...",
      "published_at": "2026-04-11T08:30:00+00:00",
      "scraped_at": "2026-04-11T09:00:02+00:00"
    }
  ],
  "count": 1
}
```

## Architecture

```
src/marcel_core/skills/integrations/news/
    __init__.py     # @register handlers: sync, search, recent
    sync.py         # Feed config loading + concurrent fetch + dedupe
    cache.py        # SQLite upsert + query
```

### Sync (`sync.py`)

Loads the feed configuration from YAML (user override → bundled default), then spawns one `asyncio.Task` per feed URL. Articles from each feed are tagged with the source name, filtered by the source's `exclude_categories`, and collected. Duplicates within a single sync are removed by URL. The remaining list is diff-ed against the database, and only previously-unseen articles are inserted.

Feed fetching itself lives in `tools/rss.py` (`fetch_feed`) — the same helper used by the standalone `rss_fetch` tool. Extracting it meant `news.sync` could reuse the parser without going through an agent tool call.

### Cache (`cache.py`)

SQLite database at `~/.marcel/users/{slug}/cache/news.db`. One `articles` table keyed by URL with columns for title, source, topic, description, `published_at`, and `scraped_at`. The cache is per-user so each household member sees only their own filter state (e.g. which articles they've already been shown in a digest).

## Design notes

The original news integration had the LLM call a single `rss_fetch` tool once per feed, concatenate results, and parse them. It worked but was slow, expensive, and fragile to prompt drift. Moving fetch + dedupe into code (ISSUE-065) made sync ~20× faster, cut token usage to zero, and let us write straightforward unit tests.

The guiding question for any tool is: *does this need LLM judgment?* For fetch + parse + dedupe, the answer is no — that work is deterministic and belongs in code. The LLM's role is interpreting the results ("summarize the top 5 articles about the climate bill"), not running the pipeline.
