# ISSUE-002: Flat-file storage layer

**Status:** Closed
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, phase-1

## Capture
**Original request:** Store memory, conversations, and user data as flat files rather than SQLite. Use a conversation index with short descriptions.

**Resolved intent:** Implement the `storage/` module in `marcel-core` — all read/write operations for users, conversations, and distilled memory. No database. Files are markdown and JSON. The agent will consume output from this layer as plain text context injected into its system prompt.

## Description

The data directory structure per user:

```
data/
  users/
    {user_slug}/
      profile.md              # name, preferences, known facts (free-form markdown)
      channel_ids.json        # {"cli": "fingerprint", "telegram": "12345"}
      conversations/
        index.md              # one line per convo: date, filename, description
        2026-03-26T14-32.md   # full transcript
      memory/
        index.md              # one line per topic file: filename, one-liner
        calendar.md
        family.md
        ...
```

**Conversation file format:**
```markdown
# Conversation — 2026-03-26T14:32 (channel: cli)

**User:** What's on my calendar?
**Marcel:** You have a dentist appointment Tuesday at 10am.
```

**Conversation index format:**
```markdown
- [2026-03-26T14-32](2026-03-26T14-32.md) — calendar check, dentist appointment
```

**Memory index format:**
```markdown
- [calendar.md](calendar.md) — appointment preferences, recurring events
- [family.md](family.md) — family members, birthdays
```

### Public API for `storage/`

```python
# User
def user_exists(slug: str) -> bool
def load_user_profile(slug: str) -> str          # raw markdown
def save_user_profile(slug: str, content: str) -> None

# Conversations
def new_conversation(slug: str, channel: str) -> str     # returns filename stem
def append_turn(slug: str, filename: str, role: str, text: str) -> None
def load_conversation(slug: str, filename: str) -> str   # raw markdown
def load_conversation_index(slug: str) -> str            # raw markdown
def update_conversation_index(slug: str, filename: str, description: str) -> None

# Memory
def load_memory_index(slug: str) -> str                  # raw markdown
def load_memory_file(slug: str, topic: str) -> str       # raw markdown
def save_memory_file(slug: str, topic: str, content: str) -> None
def update_memory_index(slug: str, topic: str, description: str) -> None
```

All writes use write-to-temp-then-rename for atomicity. The module keeps a per-user `asyncio.Lock` to prevent concurrent write races.

## Tasks
- [✓] Create `data/users/shaun/` seed directory with `profile.md` and empty index files
- [✓] `storage/__init__.py`: exports all public functions
- [✓] `storage/users.py`: `user_exists`, `load_user_profile`, `save_user_profile`
- [✓] `storage/conversations.py`: `new_conversation`, `append_turn`, `load_conversation`, `load_conversation_index`, `update_conversation_index`
- [✓] `storage/memory.py`: `load_memory_index`, `load_memory_file`, `save_memory_file`, `update_memory_index`
- [✓] `storage/_locks.py`: per-user asyncio lock registry
- [✓] `storage/_atomic.py`: atomic write helper (write temp + rename)
- [✓] Tests: round-trip write/read for each function; all 30 tests pass
- [✓] Docs: `docs/storage.md` — file layout, format specs, public API

## Relationships
- Depends on: [[ISSUE-001-marcel-core-server-scaffold]]
- Blocks: [[ISSUE-003-agent-loop]]

## Implementation Log

### 2026-03-26 - LLM Implementation
**Action**: Implemented full flat-file storage layer
**Files Created**:
- `src/marcel_core/storage/_atomic.py` — atomic write helper (temp file + os.rename)
- `src/marcel_core/storage/_locks.py` — per-user asyncio.Lock registry
- `src/marcel_core/storage/_root.py` — data root resolution (env var, .git walk, test override)
- `src/marcel_core/storage/users.py` — user_exists, load_user_profile, save_user_profile
- `src/marcel_core/storage/conversations.py` — new_conversation, append_turn, load_conversation, load_conversation_index, update_conversation_index
- `src/marcel_core/storage/memory.py` — load_memory_index, load_memory_file, save_memory_file, update_memory_index
- `src/marcel_core/storage/__init__.py` — re-exports all public functions + get_lock
- `data/users/shaun/profile.md` — seed developer profile
- `data/users/shaun/conversations/index.md` — empty (header only)
- `data/users/shaun/memory/index.md` — empty (header only)
- `data/.gitkeep` — keeps data/ tracked by git
- `tests/core/test_storage.py` — 30 tests covering full public API
- `docs/storage.md` — data layout, file format specs, full public API docs
**Files Modified**:
- `src/marcel_core/storage/__init__.py` — populated (was empty)
- `.gitignore` — added exceptions to allow seed data for shaun
- `mkdocs.yml` — registered storage.md under nav
**Commands Run**: `uv run pytest tests/core/test_storage.py -v`
**Result**: 30 passed, 0 failed
**Next**: ISSUE-003 (agent loop) is now unblocked
