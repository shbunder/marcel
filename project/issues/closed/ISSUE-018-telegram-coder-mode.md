# ISSUE-018: Telegram Coder Mode — Self-Modification via Claude Code SDK

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Marcel
**Priority:** Medium
**Labels:** feature, telegram, agent, self-modification

## Capture
**Original request:** "Telegram coder mode — enable self-modification via Telegram using Claude Code SDK preset"

**Follow-up Q&A:** Preceded by a design discussion exploring four architecture options. The Claude Code SDK's `tools={"type": "preset", "preset": "claude_code"}` preset was identified as the best approach — it gives a spawned agent the full Claude Code toolset without needing a raw shell tool or CLI subprocess.

Design discussion resolved:
- No classifier — use explicit `/code` command prefix instead
- Use `system_prompt` and `tools` presets (not custom)
- `bypassPermissions` + `can_use_tool` for restricted-file guardrails
- `max_turns=75`, default model, global `asyncio.Lock`
- Session mode tracking: `mode` field in sessions.json for coder follow-ups
- `resume=session_id` for coder conversation continuity (captured from StreamEvent)
- Add `/new`, `/done` commands and auto-new on inactivity

**Resolved intent:** When a user sends `/code <request>` via Telegram, Marcel enters coder mode: spawns a Claude Code agent with the full toolset, follows the issue lifecycle, and responds with the commit + summary. Follow-up messages continue the coder session until the task completes, the user sends `/done`, or the session times out. `/new` starts a fresh conversation. Auto-new triggers after 6h of inactivity.

## Description

The Telegram agent only has `cmd` (HTTP skills), `notify`, and iCloud tools. It cannot write files, run shell commands, or interact with git. This makes it unable to act on any code-change request.

The fix: an explicit `/code` command routes to a coder agent spawned with the `claude_code` tool preset. Session state tracks whether the chat is in coder mode so follow-up messages continue the same coder session. The existing watchdog handles restart and rollback after code changes.

Additionally, Telegram sessions get `/new` (fresh conversation) and auto-new on inactivity (6h) to prevent stale context accumulation.

## Tasks
- [✓] ISSUE-018-a: Update session state — add `mode`, `coder_session_id`, `last_message_at` fields to sessions.json
- [✓] ISSUE-018-b: Create coder agent runner (`src/marcel_core/agent/coder.py`) — `run_coder_task()` with `claude_code` preset, `can_use_tool` callback for restricted files, global `asyncio.Lock`, session_id capture from StreamEvent
- [✓] ISSUE-018-c: Update webhook (`src/marcel_core/telegram/webhook.py`) — add `/code`, `/done`, `/new` commands; route coder-mode messages to `run_coder_task`; auto-new on 6h inactivity; 600s timeout for coder tasks
- [✓] ISSUE-018-d: Export new functions from `src/marcel_core/agent/__init__.py`
- [✓] ISSUE-018-e: Write tests (`tests/core/test_coder.py` + additions to `tests/core/test_telegram.py`)
- [✓] ISSUE-018-f: Run `make check`, update docs, version bump

## Relationships
- Related to: [[ISSUE-013-fix-telegram-agent-hang]] (same root cause — Telegram agent limitations)
- Related to: [[ISSUE-014-sop-telegram-issue-tracking]] (coder mode must follow the Telegram SOP)

## Comments
### 2026-03-29 - Design
Architecture decision: Option B (Claude Code SDK with `claude_code` preset) chosen over raw shell tool (fragile), CLI subprocess (overhead), or SDK sub-agent mechanism (`AgentDefinition.tools` doesn't support presets). See conversation for full trade-off analysis.

### 2026-03-29 - Design Revision
Classifier dropped in favour of explicit `/code` command prefix. Session state expanded to track coder mode and SDK session ID for `resume`-based follow-ups. Added `/new` command and auto-new-on-inactivity (6h) to prevent stale Telegram conversations. Spike confirmed SDK accepts `tools` preset, `system_prompt` preset, `cwd`, `can_use_tool`, and `max_budget_usd` fields.

## Implementation Log

### 2026-03-29 14:00 - LLM Implementation
**Action**: Implemented coder mode for Telegram
**Files Modified**:
- `src/marcel_core/telegram/sessions.py` — Expanded session state from flat `{chat_id: conversation_id}` to `{chat_id: SessionState}` with mode, coder_session_id, last_message_at fields. Added enter/exit coder mode, auto-new on inactivity, reset_session, legacy migration.
- `src/marcel_core/agent/coder.py` — Created coder agent runner using claude_code preset for tools and system prompt. Includes restricted-file guard (CLAUDE.md, auth/), global asyncio.Lock, session_id capture from StreamEvent, resume support.
- `src/marcel_core/telegram/webhook.py` — Added /code, /done, /new commands. Coder-mode routing for follow-ups. Auto-new on 6h inactivity. 600s timeout for coder tasks. Refactored _process_message into _process_assistant_message and _process_coder_message.
- `src/marcel_core/agent/__init__.py` — Exported run_coder_task and CoderResult.
- `tests/core/test_coder.py` — 12 tests: restricted file guard (7), coder runner (5 including concurrency).
- `tests/core/test_telegram.py` — 15 new tests: coder mode state (4), auto-new inactivity (4), legacy migration (1), webhook commands (6). Updated 1 existing test for new session format.
- `docs/channels/telegram.md` — Documented commands, coder mode, safety guardrails, auto-new.
- `pyproject.toml` + `src/marcel_core/__init__.py` — Version bump 0.2.3 → 0.3.0
**Commands Run**: `make check` (lint, typecheck, tests)
**Result**: 136 tests passing, 0 lint errors on changed files, 0 type errors
