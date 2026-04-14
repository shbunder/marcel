# ISSUE-075: Browser — improve JavaScript-heavy site handling

**Status:** Closed
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

- [✓] Add `trafilatura` to [pyproject.toml](../../../pyproject.toml) dependencies and run `uv sync`
- [✓] Add `extract_readable(page) -> str` helper in [src/marcel_core/tools/browser/manager.py](../../../src/marcel_core/tools/browser/manager.py) — calls `page.content()`, pipes through Trafilatura, falls back to `inner_text('body')` on empty, truncates to `MAX_READABLE_CHARS` (8000)
- [✓] Add `browser_read(ctx)` in [src/marcel_core/tools/browser/pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py)
- [✓] Wire `case 'read'` in [src/marcel_core/tools/web/dispatcher.py](../../../src/marcel_core/tools/web/dispatcher.py); update the `web` tool docstring so the model knows when to reach for `read`
- [✓] Update `browser_navigate` in [pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py) with the staged bounded-wait sequence (domcontentloaded → networkidle 3s → body-has-text 2s, all best-effort)
- [✓] Update `browser_navigate` to detect sparse a11y snapshots and append a `Readable content:` block via `extract_readable`
- [✓] Unit test: direct `extract_readable` tests in [tests/browser/test_manager.py](../../../tests/browser/test_manager.py) — React-styled HTML, inner_text fallback, failure sentinels, truncation branch
- [✓] Unit test: `browser_navigate` appends `Readable content:` on sparse snapshot ([tests/tools/test_browser_tools.py](../../../tests/tools/test_browser_tools.py)::test_navigate_sparse_snapshot_appends_readable)
- [✓] Unit test: `web(action="read")` dispatches to `browser_read` ([tests/tools/test_web_dispatcher.py](../../../tests/tools/test_web_dispatcher.py)::test_read_routes)
- [✓] Update [docs/web.md](../../../docs/web.md) — new `read` row in the action table, new *JavaScript-heavy sites* section explaining Trafilatura + staged waits, three-tier hierarchy updated
- [✓] Update [src/marcel_core/defaults/skills/web/SKILL.md](../../../src/marcel_core/defaults/skills/web/SKILL.md) — agent-facing prompt teaches the model about `read`, workflows updated (the on-disk seeded copy at `~/.marcel/skills/web/SKILL.md` was also synced)
- [✓] Verify [mkdocs.yml](../../../mkdocs.yml) nav already contains `web.md` (line 17: `- Web Tool: web.md` — no change needed)
- [⚒] Live smoke test: `marcel: read https://www.hellofresh.be/recipes and tell me what recipes are shown` — **offline substitute passed** (Trafilatura on a React-styled HelloFresh-shaped fixture extracts all 3 recipe descriptions). Browser-level test is **blocked in dev sandbox** (Chromium headless-shell needs `libatk-1.0.so.0`); must be verified after next Docker redeploy where the full Playwright system deps are installed.
- [⚒] Live regression test: one known-easy site (`bbc.com/news`) and one known-hard site (`investing.com`) — **same block**; verify post-redeploy alongside the HelloFresh smoke test.
- [✓] Run `make check` — format/lint/typecheck/clippy green, 1299 tests passing, coverage 92.93%
- [✓] Bump version per [project/VERSIONING.md](../VERSIONING.md) in the closing commit — 2.8.0 → 2.9.0 (DEFAULT bump: visible fix + new tool action)
- [✓] Close via `/finish-issue` — issue moved to `closed/` in the closing commit

## Relationships

None.

## Implementation Log

### 2026-04-14 - LLM Implementation

**Action**: Added Trafilatura-backed readable-content extraction for JavaScript-heavy SPAs (React, Next.js, Vue) that collapse the Playwright accessibility tree on unsemantic `<div>` soup. New `web(action="read")` primitive plus an auto-fallback so a single `navigate` call on a page like hellofresh.be/recipes returns usable prose on the first turn.

**Files Modified**:
- [pyproject.toml](../../../pyproject.toml) — added `trafilatura>=2.0.0` to both the `browser` optional-dependencies extra and the `dev` group; version bumped 2.8.0 → 2.9.0 (in the closing commit).
- [src/marcel_core/tools/browser/manager.py](../../../src/marcel_core/tools/browser/manager.py) — new `extract_readable(page)` helper (lazy-imports `trafilatura`, renders `page.content()` after hydration, falls back to `inner_text('body')`, truncates at 8000 chars) and `_is_sparse_snapshot(text)` heuristic (empty sentinels or fewer than 5 meaningful lines). New constants `MAX_READABLE_CHARS` and `SPARSE_SNAPSHOT_LINE_THRESHOLD`.
- [src/marcel_core/tools/browser/pydantic_tools.py](../../../src/marcel_core/tools/browser/pydantic_tools.py) — `browser_navigate` now layers bounded best-effort waits on top of `domcontentloaded` (`wait_for_load_state('networkidle', 3 s)` + `wait_for_function('body.innerText.length > 200', 2 s)`), then auto-appends a `Readable content:` block when the a11y snapshot is sparse. New `browser_read(ctx)` function that returns title + URL + Trafilatura prose.
- [src/marcel_core/tools/web/dispatcher.py](../../../src/marcel_core/tools/web/dispatcher.py) — imports `browser_read`, adds `case 'read'` to the match, `'read'` to `_AVAILABLE_ACTIONS`, updates the `web` tool docstring so the model knows to reach for `read` on sparse pages and knows `navigate` auto-attaches readable content.
- [src/marcel_core/defaults/skills/web/__init__.py](../../../src/marcel_core/defaults/skills/web/SKILL.md) — agent-facing prompt updated: three-tier hierarchy mentions `read`, action table has a new row, "Reading a specific page" workflow teaches the read-after-interaction pattern, "JS-heavy sites" tip rewritten to prefer `read` over `evaluate`/`content`. Seeded copy at `~/.marcel/skills/web/SKILL.md` synced.
- [docs/web.md](../../../docs/web.md) — new action-table row for `read`, new **JavaScript-heavy sites (React, Next.js, Vue)** section documenting the staged wait strategy and the Trafilatura extraction primitive, three-tier hierarchy updated (in the closing commit).
- [tests/browser/test_manager.py](../../../tests/browser/test_manager.py) — 10 new tests: `TestIsSparseSnapshot` (5 cases: sentinels, few-lines, rich-tree, blank-line counting) and `TestExtractReadable` (5 cases: React-styled prose happy path, Trafilatura-empty → `inner_text` fallback, `content()` failure sentinel, both-empty sentinel, 8000-char truncation branch).
- [tests/tools/test_browser_tools.py](../../../tests/tools/test_browser_tools.py) — `browser_navigate` fixture mocks `wait_for_load_state`/`wait_for_function` and `extract_readable`; the stock snapshot now returns 6 lines so `test_navigate_success` stays non-sparse. Two new tests: `test_navigate_sparse_snapshot_appends_readable` (mocks `build_snapshot` → `'(Empty page)'` and asserts `Readable content:` is in the result) and `test_navigate_swallows_hydration_wait_timeouts` (both bounded waits raise — navigate still succeeds). New `TestBrowserRead` class with success + failure tests for the new `browser_read` function. Imports `browser_read`.
- [tests/tools/test_web_dispatcher.py](../../../tests/tools/test_web_dispatcher.py) — new `test_read_routes` asserts `web(action='read')` dispatches to `_browser_read` and returns its result.

**Commands Run**:
- `uv sync --extra browser` — installed `trafilatura==2.0.0` (+ `courlan`, `dateparser`, `htmldate`, `justext`, `lxml-html-clean`, `tld`, `tzlocal`).
- `uv run pytest tests/tools/test_browser_tools.py tests/tools/test_web_dispatcher.py tests/browser/test_manager.py -v` — iterative debugging (caught one test using a non-allowlisted URL).
- `make check` — ruff format, ruff check, cargo fmt, cargo clippy, pyright, full pytest suite with coverage. 1299 passed, 92.93% coverage.
- `uv run python /tmp/smoketest_trafilatura.py` — offline smoke test against a React/styled-component HelloFresh-shaped HTML fixture. All 3 recipe descriptions extracted successfully.

**Result**: Success — full quality gate green, 12 new tests (10 manager + 2 navigate/read), 2 routing tests updated. Marcel now returns usable prose from the exact class of page (React/Next.js/Vue SPAs) that previously yielded `'(Empty page)'`.

**Reflection**:

*Coverage (requirements addressed):*
- ✓ *Trafilatura wired into the browser pipeline* → `manager.extract_readable` + `browser_read` + `web(action='read')` dispatch.
- ✓ *Hydration-aware navigate* → bounded `networkidle` + `wait_for_function` body-has-text gates in `browser_navigate`, both best-effort.
- ✓ *Sparse-snapshot auto-fallback* → `_is_sparse_snapshot` + readable-content append branch in `browser_navigate`.
- ✓ *No regression on healthy pages* → `test_navigate_success` asserts `Readable content:` is **not** appended when the snapshot has ≥ 5 meaningful lines.
- ✓ *Graceful degradation when Trafilatura fails* → `inner_text('body')` fallback, `(Empty page content)` sentinel, `(Could not read page content)` sentinel on `page.content()` failure, all tested.
- ⚒ *Live browser end-to-end* → verified offline on a React-shaped fixture; full browser verification deferred to post-redeploy because the dev sandbox is missing `libatk-1.0.so.0` (headless Chromium system library). Task stays `[⚒]` until a Marcel redeploy can confirm `hellofresh.be/recipes` returns recipes in the Telegram reply.

*Shortcuts found:* none — no TODO/FIXME/HACK, no bare `except:` (all exception handlers either log or swallow a known bounded wait), no magic numbers left unnamed (8000, 3 s, 2 s, 5 are all named constants or documented inline with a why-comment).

*Scope drift:*
- **Added (minor):** the extra direct tests for `extract_readable` and `_is_sparse_snapshot` in `tests/browser/test_manager.py`. Not strictly required by the plan (those helpers live in `manager.py` which is coverage-omitted), but valuable as regression anchors for the core extraction logic — if Trafilatura's output shape changes in a future bump, the React-fixture test will catch it. Kept small and focused.
- **Not done:** live browser-level smoke test against HelloFresh/BBC/investing.com. Dev-environment block documented above. Substituted an offline Trafilatura test on a realistic React-shaped HTML fixture to validate the extraction code path.
- **Not done (out of plan):** HelloFresh account login, scroll-to-load, response caching — all explicitly out of scope per the issue.
