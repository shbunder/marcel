# ISSUE-056: Add RSS fetch tool, browser_evaluate/content, optimize news scraper

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** Medium
**Labels:** feature, tools

## Capture
**Original request:** "Add RSS fetch tool, browser_evaluate, browser_content, and optimize news scraper job"

**Follow-up Q&A:**
- User confirmed RSS feed reader should be a standalone tool (not integration skill) that uses feedparser to return structured JSON
- User wants the news scraper job to focus on headlines, tech, and finance articles

**Resolved intent:** The news scraper job wastes tokens because VRT NWS returns empty accessibility trees (JS SPA) and De Tijd blocks headless browsers (403). The agent falls back to expensive screenshots (~15 per run) and still can't reliably extract text. We need three new tools — `rss_fetch` for structured feed parsing, `browser_evaluate` for JS execution in page context, and `browser_content` for raw HTML retrieval — plus a rewritten job prompt that uses RSS as primary data source and focuses on headlines, tech, and finance.

## Description
The current news scraper job uses `claude-opus-4-6` with 150 request limit and burns through tool calls taking screenshots of pages it can't read via accessibility tree. Three root causes:

1. VRT NWS is a JS-heavy SPA — `page.accessibility.snapshot()` returns None
2. De Tijd returns 403 for headless browsers on main pages (RSS works)
3. No DOM extraction capability when accessibility tree fails

Adding `rss_fetch` eliminates the browser entirely for feed-based sources. Adding `browser_evaluate` and `browser_content` provide cheap fallbacks when the accessibility tree is empty.

## Tasks
- [✓] Add `rss_fetch` tool — standalone agent tool using httpx + xml.etree, returns structured JSON articles
- [✓] Add `browser_evaluate` tool — run JS in page context via `page.evaluate()`, return result as string
- [✓] Add `browser_content` tool — return truncated raw HTML via `page.content()`
- [✓] Register all three tools in `agent.py`
- [✓] Update browser `SKILL.md` with new tool docs
- [✓] Rewrite news scraper job system prompt to use RSS first, focus on headlines/tech/finance
- [✓] Run `make check` — all passes (695 passed)

## Relationships
- Related to: [[ISSUE-026-agui-rich-content]] (browser tools)

## Comments

## Implementation Log
### 2026-04-11 - LLM Implementation
**Action**: Added three new tools and rewrote news scraper job
**Files Modified**:
- `src/marcel_core/tools/rss.py` — New RSS feed fetcher tool (httpx + xml.etree, no new deps)
- `src/marcel_core/tools/browser/pydantic_tools.py` — Added browser_evaluate and browser_content tools
- `src/marcel_core/harness/agent.py` — Registered rss_fetch, browser_evaluate, browser_content
- `src/marcel_core/defaults/skills/browser/SKILL.md` — Documented new tools, updated fallback guidance
- `~/.marcel/skills/browser/SKILL.md` — Synced deployed copy
- `~/.marcel/users/shaun/jobs/341e749bde4b/job.json` — Rewrote prompt: RSS-first, Haiku model, 30 req limit, focus on headlines/tech/finance
**Commands Run**: `make check`
**Result**: 695 passed, 1 warning
**Key changes to job**:
- Model: claude-opus-4-6 → claude-haiku-4-5-20251001 (RSS doesn't need Opus)
- Request limit: 150 → 30 (RSS is ~3-5 tool calls)
- Dropped browser skill from required skills
- Strategy: RSS feeds first, browser_evaluate as fallback, no screenshots
