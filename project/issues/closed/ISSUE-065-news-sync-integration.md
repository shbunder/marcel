# ISSUE-065: News Sync Integration

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** LLM Implementation
**Priority:** High
**Labels:** feature, integration, performance

## Capture
**Original request:** "The news collecting job seems to be failing (and wasting lots of tokens in the process). [...] Recommend that we create a specific integration for syncing news (like the bank.sync), can the links to search for this integration be specified somewhere easily instead of hardcoding? Revisit all the tools related to this, what still makes sense, I don't want to bloat Marcel with useless tools / skills"

**Follow-up Q&A:** Analysis showed the job's system prompt told the agent to use `rss_fetch` as an integration skill call (`integration(skill="rss_fetch", ...)`), but `rss_fetch` is a direct agent tool — not an integration. All 20 RSS fetch attempts failed, triggering a browser fallback spiral that burned tokens with no results.

**Resolved intent:** Replace the LLM-driven news scraping job with a code-driven `news.sync` integration that fetches RSS feeds, deduplicates, and stores articles entirely in Python — following the `banking.sync` pattern. Feed URLs should be configurable via a YAML file, not hardcoded in a job system prompt. Remove tools/integrations that only existed to let the LLM do work that code should handle.

## Description

The current news scraping architecture has the LLM agent orchestrate 20 HTTP requests, parse XML, deduplicate, and store articles — all tasks that deterministic code can handle more reliably, faster, and at zero token cost. The `banking.sync` integration already proves this pattern: a single `integration(id="banking.sync")` call triggers Python code that handles all the API communication, parsing, and storage.

### What changes

1. **New `news.sync` integration** — Python code in `news/sync.py` that fetches all configured RSS feeds, parses them (reusing `rss.py` parsing logic), deduplicates by link, filters out already-known articles, and stores new ones via `cache.py`. Returns a summary of what was synced.

2. **Feed URL config file** — `feeds.yaml` in the news skill defaults, seeded to `~/.marcel/skills/news/feeds.yaml`. Users can add/remove sources by editing this file.

3. **Remove bloat** — `news.store` and `news.filter_new` integrations become internal-only (still in `cache.py`, just not registered as integration skills). The `rss_fetch` direct agent tool is removed from agent registration — its parsing logic is reused internally by the sync code.

4. **Simplified job** — The news scraper job becomes a trivial `integration(id="news.sync")` call. System prompt shrinks from 60+ lines to ~5. Model can be downgraded to Haiku.

### What stays

- `news.recent` and `news.search` — user-facing query integrations, still needed
- RSS parsing logic in `rss.py` — reused as a library by sync code
- News SQLite cache in `cache.py` — unchanged

## Tasks
- [✓] ISSUE-065-a: Create `src/marcel_core/defaults/skills/news/feeds.yaml` with the 20 feed URLs grouped by source, including source name and optional exclude_categories
- [✓] ISSUE-065-b: Create `src/marcel_core/skills/integrations/news/sync.py` — `sync_feeds(user_slug)` function that loads feeds.yaml, fetches all feeds (reusing `rss.py` parsing), deduplicates, filters new via `cache.filter_new_links()`, stores via `cache.upsert_articles()`, returns summary dict
- [✓] ISSUE-065-c: Register `news.sync` handler in `news/__init__.py`, remove `news.store` and `news.filter_new` from integration registry
- [✓] ISSUE-065-d: Remove `rss_fetch` from agent tool registration in `agent.py`; keep `rss.py` as internal library
- [✓] ISSUE-065-e: Update `src/marcel_core/defaults/skills/news/SKILL.md` — document `news.sync`, remove `news.store`/`news.filter_new`/`rss_fetch` sections
- [✓] ISSUE-065-f: Update the news scraper job (`341e749bde4b/job.json`) — simplify system prompt to just call `integration(id="news.sync")`, downgrade model to haiku
- [✓] ISSUE-065-g: Update tests in `tests/tools/test_news.py` and add tests for sync logic
- [✓] ISSUE-065-h: Seed `feeds.yaml` to user data dir on startup (verify loader handles this)

## Subtasks
- [✓] ISSUE-065-a: Feed config file
- [✓] ISSUE-065-b: Sync implementation
- [✓] ISSUE-065-c: Integration registry changes
- [✓] ISSUE-065-d: Remove rss_fetch agent tool
- [✓] ISSUE-065-e: Update skill docs
- [✓] ISSUE-065-f: Simplify scraper job
- [✓] ISSUE-065-g: Tests
- [✓] ISSUE-065-h: Seed feeds.yaml on startup

## Relationships
- Related to: [[ISSUE-056-rss-browser-tools-news-scraper]] (original implementation of RSS tooling)

## Comments

## Implementation Log

### 2026-04-11 - LLM Implementation
**Action**: Implemented news.sync integration replacing LLM-driven scraping
**Files Modified**:
- `src/marcel_core/defaults/skills/news/feeds.yaml` — Created: 20 RSS feed URLs for 7 Belgian sources
- `src/marcel_core/skills/integrations/news/sync.py` — Created: sync_feeds() fetches feeds concurrently, deduplicates, filters, stores
- `src/marcel_core/skills/integrations/news/__init__.py` — Registered news.sync, removed news.store and news.filter_new
- `src/marcel_core/tools/rss.py` — Extracted fetch_feed() as reusable async function; rss_fetch agent tool delegates to it
- `src/marcel_core/harness/agent.py` — Removed rss_fetch from agent tool registration
- `src/marcel_core/defaults/skills/news/SKILL.md` — Updated docs: news.sync, removed old sections
- `src/marcel_core/defaults/__init__.py` — Enhanced seeding to copy individual missing files into existing skill dirs
- `tests/tools/test_news.py` — Rewrote: removed store/filter_new handler tests, added 8 sync tests
- `tests/tools/test_rss.py` — Added 3 tests for fetch_feed()
**Commands Run**: `make check`
**Result**: All 1077 tests pass, 93% coverage, all checks green
**Reflection**:
- Coverage: 8/8 requirements addressed — feeds.yaml, sync.py, registry changes, tool removal, docs, job simplification, tests, seeding
- Shortcuts found: none — no TODOs, FIXMEs, bare excepts, or magic numbers
- Scope drift: none — enhanced default seeder to seed individual missing files into existing skill dirs (directly supports task h, not extra scope)
