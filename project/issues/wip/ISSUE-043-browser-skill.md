# ISSUE-043: Browser/Web Interaction Skill

**Status:** WIP
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
- [ ] ISSUE-043-a: Create `src/marcel_core/browser/security.py` — SSRF protection
- [ ] ISSUE-043-b: Create `src/marcel_core/browser/manager.py` — BrowserManager lifecycle
- [ ] ISSUE-043-c: Create `src/marcel_core/browser/tools.py` — MCP tool definitions (9 tools)
- [ ] ISSUE-043-d: Create `src/marcel_core/browser/__init__.py` — package exports + singleton
- [ ] ISSUE-043-e: Update `src/marcel_core/config.py` — browser settings
- [ ] ISSUE-043-f: Update `src/marcel_core/agent/sessions.py` — wire browser MCP server
- [ ] ISSUE-043-g: Update `pyproject.toml` — playwright optional dependency
- [ ] ISSUE-043-h: Update `src/marcel_core/skills/loader.py` — packages requirement type
- [ ] ISSUE-043-i: Create `.marcel/skills/browser/SKILL.md` + `SETUP.md`
- [ ] ISSUE-043-j: Write tests for security, manager, and snapshot formatting
- [ ] ISSUE-043-k: Run `make check` — all passing

## Relationships
- Related to: [[ISSUE-038-pydantic-settings-config]] (config pattern)

## Implementation Log
