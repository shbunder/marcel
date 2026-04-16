# ISSUE-062: Restructure user data directory — consolidate config, organize caches

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** Medium
**Labels:** cleanup, data, architecture

## Capture
**Original request:** "what about these files: banking_transactions.db, credentials.enc, enablebanking.pem, news.db, profile.md, telegram.json, user.json — Are they all necessary, can't we group some files like the db's in subfolder data/? And what about profile.md and user.json, they seem to almost meet the same purpose? opportunity to merge? Also, /home/shbunder/.marcel/telegram/sessions.json is not user-specific — how does this work if multiple users connect?"

**Investigation findings:**

Current `~/.marcel/users/shaun/` layout (post ISSUE-059 cleanup):
```
users/shaun/
  banking_transactions.db   # SQLite transaction cache (banking/cache.py)
  credentials.enc           # Encrypted credentials blob (credentials.py)
  enablebanking.pem         # Banking API private key (banking/client.py)
  news.db                   # SQLite article cache (news/cache.py)
  profile.md                # Identity, location, preferences, about text (users.py → system prompt)
  telegram.json             # {"chat_id": "556632386"} (sessions.py → user linking)
  user.json                 # {"role": "admin"} (users.py → auth role check)
  memory/                   # Memory files
  conversation/             # Segment-based conversations
  jobs/                     # Job scheduler
```

**Problems identified:**

1. **`user.json` and `profile.md` overlap** — `user.json` only stores `{"role": "admin"}`. This could be a frontmatter field in `profile.md` which already has structured metadata. Two files for what's conceptually one user identity record.

2. **`telegram.json` is a single-field config** — just `{"chat_id": "556632386"}`. Could be a frontmatter field in `profile.md` (channel links section) rather than a standalone file.

3. **SQLite caches clutter the root** — `banking_transactions.db` and `news.db` are cache data mixed in with config and identity files. They should live in a `cache/` subdirectory.

4. **Global `~/.marcel/telegram/sessions.json` violates User Data Rule** — stores per-chat `last_message_at` timestamps at the global level, not under `users/{slug}/`. With multiple users, all session state goes in one shared file. The timestamp is effectively per-user state.

5. **`banking_transactions.db` has a verbose name** — could be `banking.db` in a `cache/` subfolder.

**Resolved intent:** Restructure the per-user data directory for consistency and clarity. Merge `user.json` and `telegram.json` into `profile.md` frontmatter. Move SQLite caches into a `cache/` subdirectory. Relocate the global telegram session state (`last_message_at`) into the conversation channel metadata (which already tracks `last_active` per channel). Remove the global `~/.marcel/telegram/sessions.json` file. Write a migration script for existing data.

## Description

The user data directory mixes config, identity, credentials, and cache files at the same level. Small single-field JSON files (`user.json`, `telegram.json`) duplicate what `profile.md` could hold. SQLite caches belong in their own subdirectory. The global telegram sessions file violates the per-user data convention. This restructure will produce a cleaner, more predictable layout.

### Target layout
```
users/shaun/
  profile.md              # identity + role + channel links (absorbs user.json + telegram.json)
  credentials.enc         # stays (encrypted, separate for security)
  enablebanking.pem       # stays (cert file, separate)
  cache/
    banking.db            # was banking_transactions.db
    news.db               # stays
  memory/
  conversation/
  jobs/
```

Global `~/.marcel/telegram/sessions.json` → removed (last_message_at already tracked by conversation channel metadata `last_active`).

## Tasks
- [✓] ISSUE-062-a: Merge `user.json` into `profile.md` — add `role` to frontmatter, update `users.py` to read/write role from profile.md, remove `user.json` dependency
- [✓] ISSUE-062-b: Merge `telegram.json` into `profile.md` — add `telegram_chat_id` to frontmatter, update `sessions.py` to read/write from profile.md, remove `telegram.json` dependency
- [✓] ISSUE-062-c: Move SQLite caches to `cache/` — update `banking/cache.py` and `news/cache.py` path helpers, rename `banking_transactions.db` → `banking.db`
- [✓] ISSUE-062-d: Remove global `telegram/sessions.json` — `last_message_at` is already tracked by conversation channel metadata (`channel.meta.json` → `last_active`), update `sessions.py` to use that instead
- [✓] ISSUE-062-e: Write migration script — backup, move files, rewrite profile.md with frontmatter, clean up old files
- [✓] ISSUE-062-f: Update tests for new paths and profile.md frontmatter
- [✓] ISSUE-062-g: Update docs (storage.md) to reflect new layout
- [✓] Run `make check` — all passes

## Subtasks
- [✓] ISSUE-062-a: Merge user.json into profile.md
- [✓] ISSUE-062-b: Merge telegram.json into profile.md
- [✓] ISSUE-062-c: Move caches to cache/ subdirectory
- [✓] ISSUE-062-d: Remove global telegram sessions.json
- [✓] ISSUE-062-e: Write migration script
- [✓] ISSUE-062-f: Update tests
- [✓] ISSUE-062-g: Update docs

## Relationships
- Follows: [[ISSUE-059-user-data-cleanup]] (059 cleaned up history/memory; 062 continues with config/cache restructure)

## Comments
### 2026-04-11 — Investigation
Audit of `users/shaun/` root files:
- `user.json`: only `{"role": "admin"}` — 1 field, read by `users.py:get_user_role()`, written by `users.py:set_user_role()`
- `telegram.json`: only `{"chat_id": "556632386"}` — 1 field, read by `sessions.py:get_user_slug()` and `get_chat_id()`, written by `sessions.py:link_user()`
- `profile.md`: 25-line markdown with identity, location, preferences — loaded by `users.py:load_user_profile()` and injected into system prompt via `context.py`
- `banking_transactions.db`: SQLite cache, path from `banking/cache.py:_db_path()`
- `news.db`: SQLite cache, path from `news/cache.py:_db_path()`
- `credentials.enc`: encrypted blob, path from `credentials.py:_credentials_path()`
- `enablebanking.pem`: PEM cert, path from `banking/client.py`
- Global `~/.marcel/telegram/sessions.json`: per-chat `last_message_at` timestamps, path from `sessions.py:_sessions_path()`

Key insight: conversation channel metadata (`conversation/telegram/channel.meta.json`) already tracks `last_active` — the global sessions.json `last_message_at` is redundant for idle detection.

## Implementation Log
### 2026-04-11 19:15 - LLM Implementation
**Action**: Full restructure — profile.md frontmatter, cache subdirectory, sessions.json removal
**Files Modified**:
- `src/marcel_core/storage/users.py` — Rewritten: profile.md frontmatter parser, role stored in frontmatter, telegram_chat_id stored in frontmatter, find_user_by_telegram_chat_id scans profiles. Removed user.json/telegram.json dependency.
- `src/marcel_core/channels/telegram/sessions.py` — Rewritten: delegates to users.py for chat_id linking, uses conversation channel metadata for last_message_at. Removed SessionState, _sessions_path, _load_sessions, _save_sessions, _get_state, _update_state.
- `src/marcel_core/skills/integrations/banking/cache.py` — Path changed to `cache/banking.db`
- `src/marcel_core/skills/integrations/news/cache.py` — Path changed to `cache/news.db`
- `tests/core/test_storage.py` — Role tests rewritten for profile.md frontmatter
- `tests/core/test_telegram.py` — Session tests rewritten for profile.md + channel metadata
- `tests/core/test_telegram_webhook.py` — Idle test updated to use channel metadata
- `tests/harness/test_runner.py` — Admin setup uses profile.md frontmatter
- `docs/storage.md` — Updated layout diagram and API docs
- `docs/integration-banking.md` — Updated cache path
- `docs/channels/telegram.md` — Updated user lookup and linking docs
- `scripts/migrate_062_restructure.py` — Migration script
**Data Migration**:
- Merged role=admin from user.json into profile.md frontmatter
- Merged telegram_chat_id=556632386 into profile.md frontmatter
- Moved banking_transactions.db -> cache/banking.db
- Moved news.db -> cache/news.db
- Removed user.json, telegram.json, global telegram/sessions.json
**Commands Run**: `make check`
**Result**: 690 tests passing, 0 errors, 0 lint/typecheck issues
**Reflection**:
- Coverage: 8/8 tasks addressed — all requirements from resolved intent covered
- Shortcuts found: none
- Scope drift: none

## Lessons Learned

### What worked well
- Profile.md frontmatter as a key-value store for small config fields (role, chat_id) avoids single-field JSON files — one file per user instead of three
- Reusing the existing `channel.meta.json` `last_active` field for telegram idle detection eliminated the global `sessions.json` entirely — no new code needed, just removed the old
- The migration script pattern from ISSUE-059 (dry-run first, then execute) was directly reusable here

### What to do differently
- The frontmatter parser strips quotes but doesn't handle all edge cases (e.g., values with colons inside quotes). For now this is fine since all values are simple strings, but if profile.md grows more complex fields, a proper YAML parser might be needed
- Should have checked `uv.lock` changes earlier — the version bump from issue 061 on main caused a diff that was distracting during pre-close verification

### Patterns to reuse
- Profile.md frontmatter for per-user structured config: `_parse_profile()` + `_serialize_profile()` + `_update_profile_field()` — simple, no dependencies, works with any key-value pair
- Delegating session state to an existing metadata store (conversation channel meta) instead of maintaining a separate state file — reduces moving parts and avoids multi-user isolation issues
- `cache/` subdirectory convention for SQLite databases — keeps caches separate from identity/config files, easy to exclude from backups or clear
