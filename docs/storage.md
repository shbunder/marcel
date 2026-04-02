# Storage

Marcel stores all user data as plain-text markdown files — no database required.
The storage module lives at `src/marcel_core/storage/` and provides a
synchronous Python API for reading and writing users, conversations, and memory.

---

## Data directory layout

```
~/.marcel/
  config.toml               # CLI configuration
  users/
    {user_slug}/
      profile.md              # display name, preferences, known facts (free-form markdown)
      channel_ids.json        # {"cli": "fingerprint", "telegram": "12345"}
      conversations/
        index.md              # one line per conversation: date, filename, short description
        2026-03-26T14-32.md   # full turn-by-turn transcript
        2026-03-25T09-11.md
      memory/
        index.md              # one line per topic file: filename, one-liner (capped at 200 lines)
        calendar.md           # distilled facts about calendar preferences (with frontmatter)
        family.md             # family members, relationships, birthdays
        shopping.md           # shopping habits, preferred stores
    _household/               # shared family memories (included in all users' context)
      memory/
        wifi.md               # household wifi credentials
        address.md            # home address, emergency contacts
  watchdog/
    restart_requested         # flag file for self-restart
    restart_result            # restart outcome (ok, rolled_back, rollback_failed)
```

The data root defaults to `~/.marcel/`.  Set the `MARCEL_DATA_DIR`
environment variable to override at runtime, or patch
`marcel_core.storage._root._DATA_ROOT` in tests.

---

## File format specifications

### Conversation file (`conversations/YYYY-MM-DDTHH-MM.md`)

The filename uses dashes for the time part (`HH-MM`) for filesystem safety.
The header displays the time with a colon.

```markdown
# Conversation — 2026-03-26T14:32 (channel: cli)

**User:** What's on my calendar this week?
**Marcel:** You have a dentist appointment Tuesday at 10am and a team lunch Thursday.

**User:** Move the dentist to Thursday afternoon.
**Marcel:** Done — dentist moved to Thursday at 3pm.
```

### Conversation index (`conversations/index.md`)

One line per conversation, appended chronologically.

```markdown
- [2026-03-26T14-32](2026-03-26T14-32.md) — calendar check, moved dentist appointment
- [2026-03-25T09-11](2026-03-25T09-11.md) — set up Google Calendar connection
- [2026-03-24T20-44](2026-03-24T20-44.md) — weekly schedule overview
```

### Memory file (`memory/{topic}.md`)

Memory files use YAML frontmatter for typed metadata, followed by free-form
prose and bullet lists.

```markdown
---
name: dentist_appointment
description: Dentist appointment on April 15 at 3pm
type: schedule
expires: 2026-04-15
confidence: told
---

Dentist appointment on April 15 at 3pm with Dr. Patel.
Shaun prefers afternoon slots.
```

Frontmatter fields:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Short identifier (snake_case) |
| `description` | recommended | One-line summary — used for relevance selection |
| `type` | yes | One of: `schedule`, `preference`, `person`, `reference`, `household` |
| `expires` | schedule only | ISO date (`YYYY-MM-DD`) — memory auto-deleted after this date |
| `confidence` | no | `told` (user stated), `observed`, or `inferred` |

Files without frontmatter still work — they're treated as untyped memories.

#### Memory types

| Type | Purpose | Lifecycle |
|------|---------|-----------|
| `schedule` | Time-bound events and appointments | Auto-pruned after `expires` date |
| `preference` | User preferences, habits, routines | Permanent (staleness warnings after 90 days) |
| `person` | People the user knows — contacts, family | Permanent |
| `reference` | Credentials, addresses, account info | Permanent |
| `household` | Shared facts for the `_household` pseudo-user | Permanent, included in all users' context |

#### Household memories

The `_household` pseudo-user (`~/.marcel/users/_household/memory/`) holds
shared family facts (wifi passwords, home address, family rules). These are
automatically included in every user's memory selection when relevant.

#### Memory lifecycle

- **Auto-expiry**: Schedule-type memories past their `expires` date are
  automatically pruned. Past appointments and deadlines are noise.
- **Staleness warnings**: Memories older than 90 days get a freshness note
  when loaded into prompts, alerting the agent to verify before acting.
- **Index cap**: `memory/index.md` is truncated at 200 lines with a warning
  comment. Keeps memory scanning fast.

### Memory index (`memory/index.md`)

One line per topic file.  `update_memory_index` appends a line only if the
topic is not already present. Capped at 200 lines.

```markdown
- [calendar.md](calendar.md) — appointment preferences, recurring events
- [family.md](family.md) — family members, relationships, birthdays
- [shopping.md](shopping.md) — shopping habits, preferred stores
```

---

## Public API

### `marcel_core.storage`

All public functions are importable directly from the package:

```python
from marcel_core.storage import (
    # Users
    user_exists, load_user_profile, save_user_profile,
    # Conversations
    new_conversation, append_turn, load_conversation,
    load_conversation_index, update_conversation_index,
    # Memory — CRUD
    load_memory_index, load_memory_file, save_memory_file, update_memory_index,
    # Memory — typed frontmatter
    MemoryType, MemoryHeader, MemorySearchResult,
    parse_frontmatter, scan_memory_headers, format_memory_manifest,
    # Memory — search and selection
    search_memory_files,
    # Memory — lifecycle
    prune_expired_memories, enforce_index_cap,
    memory_age_days, memory_freshness_note,
    # Concurrency
    get_lock,
)
```

---

### Users

```python
def user_exists(slug: str) -> bool
```
Returns `True` if `~/.marcel/users/{slug}/` exists.

```python
def load_user_profile(slug: str) -> str
```
Returns the raw markdown content of `profile.md`, or an empty string if the
file does not exist.

```python
def save_user_profile(slug: str, content: str) -> None
```
Writes `content` to `profile.md` atomically.  Creates the user directory if
needed.

---

### Conversations

```python
def new_conversation(slug: str, channel: str) -> str
```
Creates a new conversation file and returns the filename stem
(e.g. `"2026-03-26T14-32"`).

```python
def append_turn(slug: str, filename: str, role: str, text: str) -> None
```
Appends a turn to an existing conversation file.  `role` should be `"user"` or
`"assistant"`; assistant turns are displayed as `**Marcel:**`.

```python
def load_conversation(slug: str, filename: str) -> str
```
Returns the raw markdown of the conversation file, or empty string if missing.

```python
def load_conversation_index(slug: str) -> str
```
Returns the raw markdown of `conversations/index.md`, or empty string if
missing.

```python
def update_conversation_index(slug: str, filename: str, description: str) -> None
```
Appends a new entry to `conversations/index.md`.  Creates the file if needed.

---

### Memory

```python
def load_memory_index(slug: str) -> str
```
Returns the raw markdown of `memory/index.md`, or empty string if missing.

```python
def load_memory_file(slug: str, topic: str) -> str
```
Returns the raw markdown of `memory/{topic}.md`, or empty string if missing.

```python
def save_memory_file(slug: str, topic: str, content: str) -> None
```
Writes `content` to `memory/{topic}.md` atomically.

```python
def update_memory_index(slug: str, topic: str, description: str) -> None
```
Appends an entry to `memory/index.md` if the topic is not already present.
Creates the file if needed.

#### Typed memory

```python
def scan_memory_headers(slug: str) -> list[MemoryHeader]
```
Reads the first ~2KB of each `.md` file in the user's memory dir, parses
frontmatter, and returns headers sorted newest-first by mtime.  Cheap
operation — does not load full file content.

```python
def search_memory_files(slug: str, query: str, *, type_filter=None, max_results=10, include_household=True) -> list[MemorySearchResult]
```
Keyword search across filenames, frontmatter, and body content.  Results
ranked: metadata matches first, then body matches, both sorted by recency.

```python
def prune_expired_memories(slug: str, today=None) -> list[str]
```
Deletes schedule-type memories whose `expires` date has passed.  Returns
list of pruned filenames.

```python
def enforce_index_cap(slug: str, max_lines=200) -> bool
```
Truncates `memory/index.md` at `max_lines`, appending a warning comment.
Returns `True` if truncation occurred.

---

### Concurrency

```python
def get_lock(slug: str) -> asyncio.Lock
```
Returns the `asyncio.Lock` for the given user slug (creating it if necessary).
The API layer should acquire this lock before calling any storage write
function when handling concurrent requests for the same user.

Storage functions themselves are **synchronous** — the lock is for the async
API layer to coordinate around them.

---

## User-specific data

User-specific information — integration credentials, personal preferences, per-user facts — **must** be stored under `~/.marcel/users/{slug}/`, not in `.env` or `.env.local`.

| Type of data | Where it goes |
|---|---|
| Core identity (name, timezone, language) | `~/.marcel/users/{slug}/profile.md` |
| Integration facts (which Apple ID, which calendar) | `~/.marcel/users/{slug}/memory/{topic}.md` |
| Preferences, habits, known facts | `~/.marcel/users/{slug}/memory/{topic}.md` |
| System-wide API keys / ports / feature flags | `.env` / `.env.local` |
| Runtime secret that can't live in a file (e.g. password) | `.env.local` — reference its location in the memory file, do not copy the value |

This rule exists so that adding a second user never requires touching environment files, and so that each user's data is self-contained, auditable, and deletable.

---

## Atomicity

Every write goes through `_atomic.atomic_write(path, content)`:

1. Write to a temp file in the same directory (via `tempfile.mkstemp`).
2. `os.rename(tmp, path)` — atomic on POSIX systems.
3. On error: delete the temp file and re-raise.

This guarantees that readers never see a partially-written file.
