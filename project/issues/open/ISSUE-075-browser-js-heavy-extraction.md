# ISSUE-075: Browser — improve JavaScript-heavy site handling

**Status:** Open
**Created:** 2026-04-13
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** bug, feature

## Capture

**Original request:**
> check the telegram history, Marcel had quite some issue tyring to read the hello fresh website due to it being javescrip-heavy, do a deep internet analysis to see if there are ways to improve the browser to cope with this? are there solutions in ~/repos/openclaw or ~/repos/clawcode??

**Follow-up Q&A:** None — approach confirmed via plan-mode review and approval of `/home/shbunder/.claude/plans/piped-swimming-micali.md` on 2026-04-13.

**Resolved intent:** Marcel's `web` tool already runs headless Chromium via Playwright, but it still fails on JavaScript-heavy SPAs like HelloFresh. The user expects Marcel to be able to read such pages. The fix is not "add a browser" (Marcel has one), but to upgrade the wait strategy and content-extraction path that sit on top of Playwright — specifically, wait long enough for React hydration to complete and give the model a readable-prose extraction primitive that does not collapse on unsemantic `<div>` soup.

## Description

### Root cause

Confirmed by reading [src/marcel_core/tools/browser/manager.py](../../../src/marcel_core/tools/browser/manager.py) and [src/marcel_core/tools/browser/pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py):

1. `browser_navigate` uses `wait_until='domcontentloaded'`, which fires **before** React/Next.js hydration — the DOM is still skeletal when Playwright reads it.
2. `build_snapshot` at [manager.py:192](../../../src/marcel_core/tools/browser/manager.py#L192) skips every node whose role is `'none'`, `'generic'`, or empty with no name. Hydrated React output is exactly this: thousands of styled-component `<div>` wrappers with no ARIA roles, so the a11y tree collapses to near-empty.
3. The only fallback, `web(action="content")`, returns raw HTML — too noisy and long for the LLM to work with.

On 2026-04-13 Shaun asked for "this week's HelloFresh recipes". Marcel navigated to `hellofresh.be/recipes`, got an empty snapshot, tried `content` (wall of styled-component HTML), and gave up with *"The page is very JavaScript-heavy and not rendering the actual recipe content."*

### Research summary

- **openclaw** (`~/repos/openclaw/src/agents/tools/web-fetch.ts`) and **clawcode** (`~/repos/clawcode/tools/WebFetchTool/utils.ts`) are both static-HTML-only fetchers (axios + Readability / turndown). Neither renders JS. Marcel is already strictly more capable on the browser axis — the idea worth borrowing is the **Readability-style content extraction** step applied to `page.content()` *after* hydration.
- 2026 consensus for Playwright + LLM pipelines is **Trafilatura** on the rendered HTML ([url2md4ai](https://github.com/mazzasaverio/url2md4ai), [JustToThePoint benchmark](https://www.justtothepoint.com/code/scrape/)). Trafilatura is pure-Python, MIT-compatible, no native deps.
- `wait_for_load_state('networkidle')` is discouraged as a primary gate (modern SPAs never idle) but fine as a **bounded** secondary signal. Full sources in the plan file below.

### Approach

Three composable changes to [src/marcel_core/tools/browser/](../../../src/marcel_core/tools/browser/):

1. **New `web(action="read")` action** — pipes `page.content()` through Trafilatura → markdown, truncated to `MAX_SNAPSHOT_CHARS`. Falls back to `page.inner_text('body')` if Trafilatura returns empty. Sits between `snapshot` (too terse on SPAs) and `content` (raw HTML, too noisy). Keeps `content` raw per its documented contract in [docs/web.md](../../../docs/web.md).
2. **Staged hydration wait in `browser_navigate`** — `domcontentloaded` (as today) → bounded `wait_for_load_state('networkidle', timeout=3000)` → bounded `wait_for_function` for `body.innerText.length > 200` with 2 s timeout. All three are best-effort; action never fails because of a wait.
3. **Auto-fallback when a11y snapshot is sparse** — if `build_snapshot` returns fewer than N meaningful lines, `browser_navigate` automatically appends a `Readable content:` block from Trafilatura so the model gets usable text on the first call.

Full plan at `/home/shbunder/.claude/plans/piped-swimming-micali.md`.

### Out of scope (intentionally)

- **HelloFresh account login.** The user also noted Marcel can't see their personal weekly menu because it's behind auth. Separate issue if desired — not in this one.
- **Scroll-to-load / infinite scroll.** Not needed for HelloFresh; existing `web(action="scroll")` covers it.
- **Response caching.** Explicitly deferred in [docs/web.md](../../../docs/web.md#L184).

## Tasks

- [ ] Add `trafilatura` to [pyproject.toml](../../../pyproject.toml) dependencies and run `uv sync`
- [ ] Add `extract_readable(page) -> str` helper in [src/marcel_core/tools/browser/manager.py](../../../src/marcel_core/tools/browser/manager.py) — calls `page.content()`, pipes through Trafilatura, falls back to `inner_text('body')` on empty, truncates to `MAX_SNAPSHOT_CHARS`
- [ ] Add `browser_read(ctx)` in [src/marcel_core/tools/browser/pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py)
- [ ] Wire `case 'read'` in [src/marcel_core/tools/web/dispatcher.py](../../../src/marcel_core/tools/web/dispatcher.py); update the `web` tool docstring so the model knows when to reach for `read`
- [ ] Update `browser_navigate` in [pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py) with the staged bounded-wait sequence (domcontentloaded → networkidle 3s → body-has-text 2s, all best-effort)
- [ ] Update `browser_navigate` to detect sparse a11y snapshots and append a `Readable content:` block via `extract_readable`
- [ ] Unit test: `tests/tools/browser/test_extract_readable.py` — feed Trafilatura a React-style generic-div HTML fixture, assert the extracted markdown contains expected prose
- [ ] Unit test: assert `browser_navigate` includes `Readable content:` when snapshot is sparse (mock `page.accessibility.snapshot()` to return `None` / empty node)
- [ ] Unit test: `web(action="read")` dispatches to `browser_read`
- [ ] Update [docs/web.md](../../../docs/web.md) — document the `read` action (new row in action table, new section explaining it) and the staged navigate wait strategy; add `trafilatura` mention under Known limitations → removed
- [ ] Verify [mkdocs.yml](../../../mkdocs.yml) nav already contains `web.md` (no change expected)
- [ ] Live smoke test: `marcel: read https://www.hellofresh.be/recipes and tell me what recipes are shown` — expect a list of recipes in Marcel's reply
- [ ] Live regression test: one known-easy site (`bbc.com/news`) and one known-hard site (`investing.com`) still work via `navigate`
- [ ] Run `make check` and fix any format/lint/typecheck/coverage failures
- [ ] Bump version per [project/VERSIONING.md](../VERSIONING.md) in the closing commit
- [ ] Close via `/finish-issue` — do not leave in `wip/`

## Relationships

None.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
