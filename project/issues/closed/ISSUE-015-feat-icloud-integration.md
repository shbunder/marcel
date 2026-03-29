# ISSUE-015: iCloud Integration (Calendar, Notes, Mail)

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Claude Code
**Priority:** Medium
**Labels:** feature, integration, icloud

## Capture
**Original request:** iCloud integration for calendar, notes, and mail — enabling Marcel to read Apple Calendar events, Notes, and Mail via the agent tool loop.

**Resolved intent:** Add a `marcel_core.icloud` package exposing an in-process MCP server with three tools (`icloud_get_calendar_events`, `icloud_get_notes`, `icloud_search_mail`). The server is registered in the agent runner alongside the skills MCP server, making these tools available to Claude during every conversation turn.

## Description

The iCloud package provides read access to Apple iCloud data via the `pyicloud` library. Credentials (Apple ID + app-specific password) are stored in `.env.local` per the User Data Rule. The integration adds `.env` template vars and documents the storage convention in `docs/storage.md`.

## Tasks
- [✓] `src/marcel_core/icloud/client.py` — pyicloud client initialisation and caching
- [✓] `src/marcel_core/icloud/tool.py` — MCP server with calendar/notes/mail tools
- [✓] `src/marcel_core/icloud/__init__.py` — public API (`build_icloud_mcp_server`)
- [✓] `.env` — added `ICLOUD_APPLE_ID` and `ICLOUD_APP_PASSWORD` template vars
- [✓] `docs/storage.md` — user-specific data rule documented
- [✓] `uv.lock` — updated after dependency changes

## Implementation Log

### 2026-03-29 - Claude Code
**Action**: Committed previously implemented but unstaged iCloud integration files
**Files Modified**:
- `src/marcel_core/icloud/` — new package (client, tool, init)
- `.env` — iCloud credential template vars
- `docs/storage.md` — user data storage rule
- `uv.lock` — lock file update
**Result**: iCloud package now tracked in git; integration was already wired into runner.py
