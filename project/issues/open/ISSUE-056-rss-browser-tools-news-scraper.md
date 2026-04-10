# ISSUE-056: Add RSS fetch tool, browser_evaluate/content, optimize news scraper

**Status:** Open
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
- [ ] Add `rss_fetch` tool — standalone agent tool using feedparser, returns structured JSON articles
- [ ] Add `browser_evaluate` tool — run JS in page context via `page.evaluate()`, return result as string
- [ ] Add `browser_content` tool — return truncated raw HTML via `page.content()`
- [ ] Register all three tools in `agent.py`
- [ ] Update browser `SKILL.md` with new tool docs
- [ ] Rewrite news scraper job system prompt to use RSS first, focus on headlines/tech/finance
- [ ] Run `make check` — all passes

## Relationships
- Related to: [[ISSUE-026-agui-rich-content]] (browser tools)

## Comments

## Implementation Log
