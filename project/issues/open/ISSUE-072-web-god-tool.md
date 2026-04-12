# ISSUE-072: Web god-tool — search + browse unified

**Status:** Open
**Created:** 2026-04-12
**Assignee:** LLM
**Priority:** High
**Labels:** feature

## Capture

**Original request:** "I just asked marcel to give an update to Parix Roubaix, it responded it was busy, but then no response, can you check what went wrong?"

**Follow-up Q&A:**

1. After diagnosing the failure (seg-0005.jsonl: agent called `marcel.notify` "Checking...", navigated to procyclingstats and cyclingnews, got empty accessibility trees on both, ended the turn on "The page is JavaScript-heavy. Let me try a different approach." with no follow-up tool call), Shaun asked: *"yes I got the line, so that's okay. it's unfortunate that this is the end result, what we can we do to improve, think carefully please :p"*.
2. I proposed three fixes ranked by leverage: (1) Anthropic native `web_search`, (2) browser snapshot `innerText` fallback, (3) system-prompt rule against "let me try X" stubs. Shaun replied: *"I agree, can we natively build websearch such that it works for all models? not only anthropic models? Again see if there is something in the following repos that can help: ~/repos/openclaw, ~/repos/clawcode"*.
3. Initial plan sketched a `web_search` tool ported from openclaw's DuckDuckGo client. Shaun asked: *"wait before, continuing, how does this link to the browser skill then? It feels like there is overlap? shouldn't we unify? (browsing the web and doing web-searched?!)"*.
4. I proposed folding search into the existing browser skill as documentation-level unification only. Shaun asked: *"yes, re-enter plan mode and very critically analyse this plan, this seems like a powerful capability that has to work right"*.
5. On critical review I found several issues: DDG scraping is too fragile as a default; the skill auto-load mechanism is integration-tool-specific so the tool docstring is the real hierarchy carrier (not the skill doc); caching with a fixed TTL is wrong for live-event queries. Revised plan used Brave API primary + DDG fallback, kept the skill name `browser`, and folded the "let me try" stub mitigation into this change.
6. Shaun pushed further: *"last remark. the browser / web tool feels to fit in the following logic of powerful god-like tools: integrations / marcel / bash / browser (or web)"* — pointing out that the four-umbrella-tool pattern is the architectural target and browser tools are the inconsistent outlier (11 separate tools instead of one dispatcher).
7. I asked whether to ship the full `web` umbrella refactor now or defer. Shaun chose the full refactor now.

**Resolved intent:** Ship a single `web(action=...)` god-tool that unifies web search and browser interaction, consistent with the existing `marcel` / `integration` / `bash` dispatcher pattern. Twelve actions: `search` (new) plus the eleven existing browser operations. Brave Search API is the primary backend (free tier 2000/month, stable JSON contract); DuckDuckGo HTML scraping is the fallback for installs without `BRAVE_API_KEY`. Per-turn rate limit of 5 searches to protect the free tier. Deterministic `Search error: <reason>` / `Browser error: <reason>` strings for every failure branch. Tool docstring carries the three-tier cost/capability hierarchy (search → navigate → interact) plus an explicit rule against ending a turn on a forward-looking stub without calling a tool. Existing `tools/browser/` module stays untouched — the dispatcher imports its functions directly, so `test_browser_tools.py` continues to work unchanged. The `browser` skill is renamed to `web` with a 5-line seeder migration that removes stale `~/.marcel/skills/browser/` on first startup post-upgrade. Reliability is the dominant design constraint — Shaun's framing: *"this seems like a powerful capability that has to work right"*.

## Description

Marcel currently has no web search primitive. When the Paris-Roubaix query failed, the agent's only web-access tool was `browser_navigate`, and both target sites returned empty accessibility trees. The agent tried twice and gave up.

Beyond the immediate fix, the web access surface is architecturally inconsistent with the rest of Marcel. The project already has three god-tools — `integration` (external APIs), `marcel` (internal capabilities), `bash` (environment) — each a single dispatcher that routes to per-action implementations. Browser capability is the outlier: 11 separate `browser_*` tools plus nothing for search. This change ships the missing fourth god-tool (`web`) while also adding the search primitive that should have existed all along.

**Architectural target:**

```
integration(id="...", params={...})    # external service APIs
marcel(action="...", ...)               # Marcel internal capabilities
bash(command="...")                     # server / environment (admin)
web(action="...", ...)                  # web interaction (NEW)
```

**Design constraints (from critical review):**

- **Brave primary, DDG fallback** — not DDG primary. Brave's JSON API is reliable and its free tier (2000/month, ~66/day) is comfortable for a household agent. DDG HTML scraping is a best-effort fallback for zero-config installs.
- **Tool docstring is the primary hierarchy carrier** — after re-reading [ISSUE-071](../closed/ISSUE-071-skill-autoload-context-aware.md), skill auto-load only fires on `integration` tool calls, so the `web` skill doc will only be read on explicit `marcel(read_skill)`. The always-visible carrier is the dispatcher docstring, which is inlined into the tool schema every turn.
- **Per-turn rate limit of 5 searches** — prevents runaway loops from burning the Brave quota. Extend `TurnState` with `web_search_count`, mirror the existing `read_skills` pattern.
- **Deterministic error strings** — `Search error: <reason>` / `Browser error: <reason>` so the model can distinguish "no results" from "quota exhausted" from "bot challenge" from "network fail" and respond appropriately.
- **"Let me try X" stub fix folded into this change** — one explicit sentence in the `web` docstring, not deferred as a follow-up. The Paris-Roubaix failure mode must not recur.
- **No caching in v1** — live-event queries need fresh data; household query hit rate is low; quota is comfortable. YAGNI.
- **Existing `tools/browser/` stays untouched** — dispatcher imports the functions directly. `test_browser_tools.py` continues to call them without modification.
- **Skill rename with loader migration** — `defaults/skills/browser/` → `defaults/skills/web/` plus a 5-line block in the seeder that removes stale `~/.marcel/skills/browser/` on first startup.

**Follow-up issues (out of scope here):**

1. `browser: snapshot should fall back to innerText on empty accessibility tree` — still wanted, less urgent now because `search` reduces dependency on `navigate` for static content.
2. Post-turn stub-response validator in the runner — conditional follow-up, only if the docstring-level mitigation proves insufficient.

Full design detail lives in the plan file at `~/.claude/plans/zazzy-discovering-liskov.md`.

## Tasks

- [ ] Create `src/marcel_core/tools/web/` package — `__init__.py`, `dispatcher.py`, `search.py`, `backends.py`, `brave.py`, `duckduckgo.py`, `formatter.py`
- [ ] Implement `BraveBackend` with error mapping (401/429/422/network → `Search error: ...`)
- [ ] Implement `DuckDuckGoBackend` — port `parseDuckDuckGoHtml`, `decodeHtmlEntities`, `decodeDuckDuckGoUrl`, `isBotChallenge` from openclaw `ddg-client.ts`
- [ ] Implement `SearchBackend` protocol, `SearchResult` dataclass, `select_backend()` factory
- [ ] Implement `formatter.format_results(results, query, backend_name)` with edge cases (0 results, clamping)
- [ ] Implement `web` dispatcher with match-statement routing to all 12 actions, mirroring `tools/marcel/dispatcher.py`
- [ ] Implement per-turn rate limit via new `TurnState.web_search_count` field
- [ ] Implement playwright-unavailable guard inside the dispatcher (`search` works, browser actions return `Browser error: playwright not installed`)
- [ ] Register `web` in `agent.py` — replace the 11 `browser_*` registrations with a single `agent.tool(web)`, outside the `if browser_is_available():` gate
- [ ] Add `brave_api_key` and `web_search_backend` settings to `config.py`
- [ ] Add `web_search_count` field to `TurnState` in `context.py`
- [ ] Unit tests: `test_web_dispatcher.py`, `test_web_search_brave.py`, `test_web_search_duckduckgo.py`, `test_web_search_formatter.py`
- [ ] Verify existing `test_browser_tools.py` still passes unchanged
- [ ] Rename `src/marcel_core/defaults/skills/browser/` → `src/marcel_core/defaults/skills/web/`
- [ ] Rewrite `SKILL.md` leading with the three-tier hierarchy, tool table for all 12 actions, remove `requires: packages: [playwright]` frontmatter so the doc is always loaded
- [ ] Rewrite `SETUP.md` covering both playwright install and Brave API key setup
- [ ] Add 5-line browser→web migration to the skill seeder in `loader.py`
- [ ] Write `docs/web.md` per `docs/CLAUDE.md`
- [ ] Run `make check` — format, lint, typecheck, tests, coverage
- [ ] Bump version per `project/VERSIONING.md`
- [ ] Close issue, push to `shaun` branch, `request_restart()`, reply on Telegram

## Relationships

- Related to: [[ISSUE-043-browser-web-interaction-skill]] — this is the natural follow-on, consolidating the browser tools behind an umbrella and adding the missing search primitive
- Related to: [[ISSUE-071-skill-autoload-context-aware]] — informed the decision to put the hierarchy in the tool docstring rather than the skill doc

## Implementation Log

<!-- Append entries here when performing development work on this issue -->
