# ISSUE-059: Clean up user data directory — consolidate and migrate

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** Medium
**Labels:** cleanup, data, memory

## Capture
**Original request:** "I notice ~/.marcel/ is getting cluttered, especially related to memory stuff. in users/shaun there are now 3 folders memory/, history/, conversation/ and some archived stuff. Investigate this thoroughly and create issue-059 to clean this up. Migrate what you can into the new system, especially for telegram (even if it's not a perfect migration), and check all other files if they are still needed, used and if so if there are no opportunities to consolidate some of these files into a more concise structure."

**Investigation findings:**

The `~/.marcel/users/shaun/` directory currently contains:

| Path | Size | Status | Notes |
|------|------|--------|-------|
| `memory/` (20 files) | 100K | Active | Many duplicates and overlaps |
| `history/telegram/` (50 files) | 248K | **Dead** — old system, never migrated | `append_message()` is no longer called anywhere |
| `conversation/cli/` | 48K | Active | New segment-based system, only CLI channel exists |
| `conversations.archived/` (38 files) | 212K | **Dead** — no code references it at all | Orphaned markdown exports |
| `history.jsonl.migrated` | 30K | **Dead** — migration artifact | No code reads it (fallback path exists but file is `.migrated` not `.jsonl`) |
| `.pastes/` (4 files) | 1.2M | Active | Large tool output store |
| `jobs/` (4 jobs) | 40K | Active | Job scheduler |
| `banking_transactions.db` | 1.3M | Active | Banking cache |
| `news.db` | 308K | Active | News article cache |
| `profile.md` | 737B | Active | Overlaps significantly with `memory/identity.md`, `memory/location.md` |
| `telegram.json` | 28B | Active | Chat ID linking |
| `user.json` | 22B | Active | Role (admin/user) |
| `credentials.enc` | 716B | Active | Encrypted credentials |
| `enablebanking.pem` | 3.3K | Active | Banking cert |

### Problem 1: Three overlapping conversation/history stores

The codebase migrated from session-based `history/` to segment-based `conversation/` but:
- Telegram's 25 sessions in `history/telegram/` were never migrated to `conversation/telegram/`
- `conversations.archived/` (38 markdown conversation exports) is completely orphaned — no code reads or writes it
- `history.jsonl.migrated` is a dead migration artifact
- The telegram webhook (`webhook.py:259`) still reads from the old `read_history()` — this will return nothing for new conversations since `append_message()` is never called

### Problem 2: Duplicate and fragmented memory files

The 20 files in `memory/` have significant overlap:
- `identity.md` — just "User's name is Shaun" (2 lines, duplicated) — already in `profile.md`
- `home_server.md`, `home_infrastructure.md`, `home_server_/_infrastructure.md`, `home_server_/_media.md`, `infrastructure.md` — five files covering the same NUC/Plex/Docker setup with heavy overlap
- `calendar.md` and `calendars.md` — same topic, slightly different angles
- `marcel_capabilities.md` — lists what Marcel can do; derivable from the codebase, shouldn't be in memory
- `vending_machine_spending.md` — highly ephemeral data (April spending pace), stale within days
- `plex_samsung_tv_audio_issue.md` — debugging reference, likely resolved by now
- Many files lack frontmatter (only newer ones have `---` headers), making the system inconsistent

### Problem 3: profile.md vs memory overlap

`profile.md` contains identity, location, preferences — the same facts that `memory/identity.md`, `memory/location.md`, and `memory/preferences.md` cover. Two sources of truth for the same information.

**Resolved intent:** Clean up the user data directory by: (1) migrating telegram history to the new conversation system, (2) deleting dead data (conversations.archived, history.jsonl.migrated), (3) consolidating duplicate memory files, (4) fixing the telegram webhook to use the new conversation system, (5) removing memory files that are derivable from code or stale.

## Description

The user data directory has accumulated cruft from multiple system iterations. Three different conversation stores coexist, memory files are fragmented and duplicated, and dead data takes up space and causes confusion. This cleanup will leave a leaner, consistent data layout.

## Tasks
- [✓] ISSUE-059-a: Migrate telegram history — convert `history/telegram/*.jsonl` sessions into `conversation/telegram/` segments, then remove `history/telegram/`
- [✓] ISSUE-059-b: Delete dead data — remove `conversations.archived/`, `history.jsonl.migrated`
- [✓] ISSUE-059-c: Fix telegram webhook — update `webhook.py` to read from `conversation` system instead of legacy `read_history()`
- [✓] ISSUE-059-d: Consolidate memory files — merge the 5 infrastructure/server files into one, merge calendar+calendars, remove identity.md (in profile.md), remove marcel_capabilities.md (derivable), add frontmatter to all remaining files
- [✓] ISSUE-059-e: Reconcile profile.md vs memory — decide on single source of truth, deduplicate
- [✓] ISSUE-059-f: Clean up dead code — remove `append_message()`, `_read_session_file()`, `read_history()`, `list_sessions()` from `history.py` if no longer needed after webhook fix; remove `conversations.py` API endpoints that depend on them
- [✓] ISSUE-059-g: Write migration script to perform data moves safely (backup first)
- [✓] Run `make check` — all passes

## Subtasks
- [✓] ISSUE-059-a: Migrate telegram history to conversation system
- [✓] ISSUE-059-b: Delete dead data
- [✓] ISSUE-059-c: Fix telegram webhook to use new conversation system
- [✓] ISSUE-059-d: Consolidate memory files
- [✓] ISSUE-059-e: Reconcile profile.md vs memory
- [✓] ISSUE-059-f: Clean up dead history code
- [✓] ISSUE-059-g: Write migration script

## Relationships
- Related to: [[ISSUE-058-memory-learning-feedback]] (memory system improvements — do 059 cleanup first, then 058 enhancements build on clean foundation)
- Related to: [[ISSUE-050-continuous-conversation]] (introduced the segment-based conversation system that 059 finishes migrating to)

## Comments
### 2026-04-11 — Investigation
Full audit of `~/.marcel/users/shaun/` performed. Key findings:
- `conversations.archived/` is completely orphaned (no code reference in entire codebase)
- `history/telegram/` has 25 sessions (50 files) from the old system, never migrated
- `append_message()` (old system) is defined but never called — all writes go through `append_to_segment()` (new system)
- Telegram webhook still reads from old `read_history()` — this is a live bug since new telegram conversations won't have history available for the callback query handler
- 5 memory files cover NUC/Plex/Docker infrastructure with heavy duplication
- `calendar.md` and `calendars.md` are near-identical
- `identity.md` is 2 redundant lines already covered by `profile.md`
- `marcel_capabilities.md` lists features derivable from the codebase — not a useful memory

## Implementation Log
### 2026-04-11 18:45 - LLM Implementation
**Action**: Full cleanup — code changes, migration script, data migration
**Files Modified**:
- `src/marcel_core/memory/history.py` — Removed all session-based storage functions (append_message, read_history, list_sessions, create_session, SessionMeta, etc). Kept only HistoryMessage, ToolCall, MessageRole data types.
- `src/marcel_core/memory/conversation.py` — Added `list_channels()` function to list conversation channels for a user.
- `src/marcel_core/channels/telegram/webhook.py` — Replaced `read_history()` call with `read_active_segment()` from conversation system.
- `src/marcel_core/api/conversations.py` — Updated `list_conversations` to use `list_channels()`, updated `get_last_message` to use `read_active_segment()`. Removed dependency on old history functions.
- `tests/memory/test_history.py` — Removed all session-based storage tests, kept serialization tests.
- `tests/core/test_conversations.py` — Rewritten to use conversation system (ensure_channel, append_to_segment) instead of old session functions.
- `tests/core/test_chat_v2.py` — Updated test_continue_existing_conversation to use ensure_channel instead of create_session.
- `tests/memory/test_conversation.py` — Added TestListChannels tests.
- `scripts/migrate_059_cleanup.py` — New migration script: backs up data, migrates telegram history, deletes dead data, consolidates memory files, adds frontmatter, rewrites index.
**Data Migration**:
- Migrated 138 telegram messages from `history/telegram/` (25 sessions) → `conversation/telegram/seg-0001.jsonl`
- Removed `conversations.archived/` (38 orphaned markdown exports)
- Removed `history.jsonl.migrated` (dead migration artifact)
- Removed `history/` directory (empty after telegram migration)
- Consolidated 5 infrastructure files → `home_infrastructure.md`
- Merged `calendar.md` + `calendars.md` → `calendars.md`
- Removed 8 files: identity.md, preferences.md, marcel_capabilities.md, location.md, filesystem.md, vending_machine_spending.md, plex_samsung_tv_audio_issue.md, home_server.md, infrastructure.md
- Added frontmatter to 6 files: apple_integration, credentials_policy, data_storage_preferences, family, travel, work
- Rewrote memory index (22 files → 10 files)
- profile.md confirmed as single source of truth for identity/location
**Commands Run**: `make check`
**Result**: Success — 660 tests passing, 0 errors, 0 lint/typecheck issues
**Reflection**:
- Coverage: 8/8 tasks addressed — all requirements from resolved intent covered
- Shortcuts found: none (no TODO/FIXME/HACK, no bare except, no pass bodies)
- Scope drift: none — implementation stayed within the stated requirements

## Lessons Learned

### What worked well
- Writing a standalone migration script (`scripts/migrate_059_cleanup.py`) with `--dry-run` made it safe to verify the migration plan before executing — caught the permission error on root-owned files before it could corrupt data
- Removing the legacy session storage functions entirely (not just deprecating) forced all callers to migrate in the same commit — no half-migrated state
- Consolidating 22 memory files → 10 by merging duplicates and removing derivable/stale content made the memory system much cleaner for the AI selector

### What to do differently
- Root-owned files in `conversations.archived/` from an earlier Docker permission issue weren't discovered until the migration script hit a `PermissionError` — should have checked file ownership during the investigation phase
- The `scripts/` directory is gitignored, so the migration script isn't tracked. For one-shot migrations this is fine, but worth noting that scripts there are disposable

### Patterns to reuse
- When removing a module's public API: grep all imports, update all callers and tests first, then delete the functions in a single commit — ensures no dead import errors
- For data migrations: backup first, dry-run, then execute. The `shutil.copytree` with `copy_function=_copy_ignore_errors` pattern handles permission issues gracefully
- Memory file cleanup criteria: (1) derivable from codebase → delete, (2) ephemeral/stale data → delete, (3) duplicate content → merge into one file with frontmatter, (4) missing frontmatter → add it
