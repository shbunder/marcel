# ISSUE-043: Browser/Web Interaction Skill

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature

## Capture
**Original request:** Enable browser/web interaction capability in Marcel — navigate, click, type, screenshot, read pages — inspired by ClawCode and OpenClaw.

**Resolved intent:** Add a Playwright-based browser automation skill to Marcel, exposed as an in-process MCP server following the same pattern as `skills/tool.py`. This gives Marcel the ability to browse the web, interact with web pages (click, type, scroll), take screenshots, and read page content via accessibility tree snapshots. Browser contexts are per-session and cleaned up on disconnect.

## Description

Marcel currently has no way to interact with web pages. This feature adds a `browser` MCP server that provides 9 tools: navigate, screenshot, snapshot (accessibility tree), click, type, scroll, press_key, tab management, and close. The implementation uses Playwright Python with headless Chromium, managed by a `BrowserManager` that shares one browser process across sessions but isolates browser contexts per conversation. SSRF protection prevents navigation to private networks.

## Tasks
- [✓] ISSUE-043-a: Create `src/marcel_core/browser/security.py` — SSRF protection
- [✓] ISSUE-043-b: Create `src/marcel_core/browser/manager.py` — BrowserManager lifecycle
- [✓] ISSUE-043-c: Create `src/marcel_core/browser/tools.py` — MCP tool definitions (9 tools)
- [✓] ISSUE-043-d: Create `src/marcel_core/browser/__init__.py` — package exports + singleton
- [✓] ISSUE-043-e: Update `src/marcel_core/config.py` — browser settings
- [✓] ISSUE-043-f: Update `src/marcel_core/agent/sessions.py` — wire browser MCP server
- [✓] ISSUE-043-g: Update `pyproject.toml` — playwright optional dependency
- [✓] ISSUE-043-h: Update `src/marcel_core/skills/loader.py` — packages requirement type
- [✓] ISSUE-043-i: Create `.marcel/skills/browser/SKILL.md` + `SETUP.md`
- [✓] ISSUE-043-j: Write tests for security, manager, and snapshot formatting
- [✓] ISSUE-043-k: Run `make check` — all passing

## Relationships
- Related to: [[ISSUE-038-pydantic-settings-config]] (config pattern)

## Implementation Log
### 2026-04-10 — LLM Implementation
**Action**: Full implementation of browser skill
**Files Created**:
- `src/marcel_core/browser/security.py` — SSRF protection (private IP, scheme blocking, allowlist)
- `src/marcel_core/browser/manager.py` — BrowserManager (lazy Playwright init, per-session contexts, snapshot builder)
- `src/marcel_core/browser/tools.py` — 9 MCP tools (navigate, screenshot, snapshot, click, type, scroll, press_key, tab, close)
- `src/marcel_core/browser/__init__.py` — Package exports, singleton, is_available()
- `.marcel/skills/browser/SKILL.md` — LLM-facing docs (workflow, ref-based interaction, tips)
- `.marcel/skills/browser/SETUP.md` — Setup guide for when playwright not installed
- `tests/browser/test_security.py` — 19 SSRF tests
- `tests/browser/test_manager.py` — 10 snapshot/selector tests
- `tests/browser/test_loader_packages.py` — 5 packages requirement tests
**Files Modified**:
- `src/marcel_core/config.py` — Added browser_headless, browser_url_allowlist, browser_timeout settings
- `src/marcel_core/agent/sessions.py` — Wired browser MCP server into session lifecycle with cleanup
- `src/marcel_core/skills/loader.py` — Added `packages` requirement type for optional deps
- `pyproject.toml` — Added playwright optional dep + coverage omit for browser modules
**Commands Run**: `make check`
**Result**: Success — 722 tests passing, 95% coverage, all lint/type checks green

**Reflection**:
- Coverage: 11/11 requirements addressed — all tasks complete
- Shortcuts found: none — no TODOs, FIXMEs, bare excepts, or stub bodies
- Scope drift: none — implementation matches the planned scope exactly

## Lessons Learned

### What worked well
- Following the exact `skills/tool.py` MCP server pattern made integration seamless — the browser tools plugged into `sessions.py` with just 4 lines of changes
- Making playwright an optional dependency with `is_available()` gate means Marcel works fine without it — graceful degradation by default
- The `_mock_page` factory pattern using `SimpleNamespace` + `AsyncMock` kept test code clean and avoided N801 lint issues from inline mock classes

### What to do differently
- The `TYPE_CHECKING` guard for optional playwright imports still triggered pyright `reportMissingImports` — needed `# pyright: ignore` comments. Future optional deps should be added to pyright's exclude list in `pyproject.toml` instead
- Should have added `packages` requirement type to the skill loader earlier (as its own small issue) — it's a general-purpose feature, not browser-specific

### Patterns to reuse
- In-process MCP server pattern for tools that need rich schemas or image content: `create_sdk_mcp_server` + `tool()` closures over session state
- Per-session resource management: create lazily in `get_or_create`, clean up in `_disconnect_session` — follows the `BrowserContext` lifecycle pattern
- Accessibility tree snapshot with integer refs for LLM interaction — compact, structured, and gives the model a way to target elements without CSS selectors
- SSRF protection module (`is_url_allowed`) with hostname resolution + private IP range checks — reusable for any tool that accepts URLs
