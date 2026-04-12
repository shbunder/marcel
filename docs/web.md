# Web Tool

Marcel's `web` tool is the single dispatcher for everything web-related:
searching, browsing, reading, and interactive flows. It mirrors the
pattern used by [marcel](skills.md) (internal utilities),
[integration](skills.md) (external APIs), and `bash` (server
environment) — one tool per axis of capability, many actions per tool.

## Why one tool instead of many

Before ISSUE-072, web access was split across eleven individual
`browser_*` tools with no search primitive at all. A single umbrella
dispatcher:

- makes the tool schema one entry instead of twelve (cheaper in tokens),
- puts the cost/capability hierarchy in one always-visible docstring
  instead of fragmenting it across eleven tool descriptions,
- mirrors the `integration` / `marcel` / `bash` pattern so the model sees
  a consistent shape for all god-tools,
- lets us extend the web surface (new search backends, new browser
  operations) without adding new tool names.

## The three-tier hierarchy

The model is instructed to pick the cheapest primitive that does the job:

1. **`web(action="search")`** — first resort for any information query.
   Stateless, no JavaScript, fast. Always cite result URLs in the reply.
2. **`web(action="navigate")` + `web(action="content")` / `web(action="evaluate")`**
   — read a specific URL, typically a search result. Handles JavaScript.
3. **`web(action="click" / "type" / "scroll" / "press_key")`** —
   multi-step interactive flows (login, form filling). Stateful browser
   session.

This hierarchy lives in the `web` tool's docstring (always visible in the
tool schema every turn) and is reinforced in
`defaults/skills/web/SKILL.md` (loaded on demand when the model calls
`marcel(action="read_skill", name="web")`).

## Actions

| Action | Purpose | Required args | Needs playwright |
|--------|---------|---------------|:---:|
| `search` | Ranked results (title, URL, snippet). Rate-limited to 5/turn. | `query` | — |
| `navigate` | Open a URL, return title + accessibility snapshot | `url` | ✓ |
| `snapshot` | Re-read accessibility tree with `[ref]` numbers | — | ✓ |
| `screenshot` | Visual PNG | — | ✓ |
| `click` | Click an element | `ref` or `selector` or `x,y` | ✓ |
| `type` | Type text into an input | `text` + `ref` or `selector` | ✓ |
| `scroll` | Scroll the page | `direction` | ✓ |
| `press_key` | Press a keyboard key | `key` | ✓ |
| `tab` | Manage browser tabs | `tab_action` | ✓ |
| `evaluate` | Run JavaScript | `script` | ✓ |
| `content` | Get raw HTML | — | ✓ |
| `close` | Close the browser session | — | ✓ |

Actions marked ✓ require Playwright. `search` always works, regardless of
whether Playwright is installed. Browser actions on a Playwright-less
install return `Browser error: playwright not installed. Only the "search"
action is available in this environment.`

## Search backends

`web(action="search")` delegates to a pluggable
[`SearchBackend`](#adding-a-new-backend). Two implementations ship today:

### Brave Search API (primary, recommended)

- Stable JSON contract, no scraping
- Free tier: 2000 queries/month, 1 query/sec
- Get a key at <https://brave.com/search/api/>

Set `BRAVE_API_KEY=...` in `.env.local` and restart Marcel. The tool
picks it up automatically.

### DuckDuckGo HTML (fallback, zero-config)

- Port of openclaw's `ddg-client.ts`
- No API key required
- Scrapes `html.duckduckgo.com/html` — **unreliable**: DuckDuckGo
  bot-challenges unpredictably

Used automatically when `BRAVE_API_KEY` is unset. The first call logs a
warning so the operator sees that reliability is degraded.

### Forcing a specific backend

For testing, `WEB_SEARCH_BACKEND=brave|duckduckgo` in `.env.local`
overrides the auto-selection. This is a safety valve, not the primary
interface.

## Rate limiting

To protect the Brave free-tier quota from runaway loops, `web(action="search")`
is capped at **5 calls per turn**. The limit is enforced via
`TurnState.web_search_count` (see
[src/marcel_core/harness/context.py](../src/marcel_core/harness/context.py))
and resets every turn because `TurnState` is constructed fresh per turn.

When the cap is reached, the tool returns:

```
Search error: per-turn search limit reached (5). Summarise what you have
or ask the user to narrow the query.
```

The error is actionable — it tells the model what to do instead of
spinning. Browser actions are not rate-limited (no external quota).

## Error contract

Every failure returns a one-line string starting with `Search error:` or
`Browser error:`. The model branches on these to decide whether to retry,
fall back, or report cleanly.

| Prefix | Example | Meaning |
|--------|---------|---------|
| `Search error: no results for "..."` | | Query returned empty; rephrase and try again once |
| `Search error: per-turn search limit reached (5)` | | Stop searching, synthesise what you have |
| `Search error: Brave API key invalid or revoked` | | Configuration issue — tell the user to check `BRAVE_API_KEY` |
| `Search error: Brave rate limit — slow down` | | Back off and retry later |
| `Search error: DuckDuckGo bot challenge — set BRAVE_API_KEY for reliable search` | | DDG fallback is degraded; tell the user to configure Brave |
| `Search error: network failure — ...` | | Transient; retry once, then report plainly |
| `Browser error: playwright not installed` | | Fall back to `search` + direct knowledge |
| `Browser error: ...` | | Playwright-specific failures |

## Adding a new backend

1. Create a new module under
   [src/marcel_core/tools/web/](../src/marcel_core/tools/web/), e.g.
   `tavily.py`.
2. Implement the `SearchBackend` protocol:

   ```python
   from marcel_core.tools.web.backends import (
       SearchBackend,
       SearchBackendError,
       SearchResult,
   )

   class TavilyBackend(SearchBackend):
       name = 'tavily'

       def __init__(self, api_key: str) -> None:
           self._api_key = api_key

       async def search(self, query: str, max_results: int) -> list[SearchResult]:
           # ... httpx call, error mapping, parsing
           return [SearchResult(title=..., url=..., snippet=...)]
   ```

3. Add a settings field in
   [src/marcel_core/config.py](../src/marcel_core/config.py):

   ```python
   tavily_api_key: str | None = None
   ```

4. Wire it into `select_backend()` in
   [src/marcel_core/tools/web/backends.py](../src/marcel_core/tools/web/backends.py)
   — decide the priority relative to Brave and DDG.
5. Add unit tests under
   [tests/tools/test_web_search_tavily.py](../tests/tools/) following the
   pattern in `test_web_search_brave.py` (mock `httpx.AsyncClient`, cover
   happy path and each error branch).

If the new backend takes a user-configurable URL (e.g. a self-hosted
SearXNG endpoint), call
[`is_url_allowed`](../src/marcel_core/tools/browser/security.py) on the
URL before any HTTP request — same SSRF protection pattern the browser
tool uses.

## Known limitations

- **No response caching.** Live-event queries ("what's the latest on X?")
  need fresh data, and household query hit rate is low, so caching was
  dropped from v1. Add later with a smarter TTL strategy if quota
  pressure appears.
- **DDG brittleness.** The DuckDuckGo fallback relies on regex parsing of
  HTML that can change without notice. Treat every DDG error as a
  suggestion to configure `BRAVE_API_KEY`.
- **No parallel search.** Each `web(action="search")` call is sequential.

## Migration note

ISSUE-072 renamed the `browser` skill (at
`~/.marcel/skills/browser/`) to `web` (at `~/.marcel/skills/web/`). The
skill seeder in
[src/marcel_core/defaults/__init__.py](../src/marcel_core/defaults/__init__.py)
detects stale `browser/` directories and removes them on the first
startup after upgrade. The migration is idempotent and runs at most once
per install — once `web/` is seeded, the migration is a no-op.

No action required by operators.
