# Storage

Marcel stores all user data as plain-text markdown files — no database required.
The storage module lives at `src/marcel_core/storage/` and provides a
synchronous Python API for reading and writing users, conversations, and memory.

---

## Data directory layout

```
~/.marcel/
  config.toml               # CLI configuration
  MARCEL.md                 # Global personal assistant instructions
  skills/                   # Skill docs loaded into agent context
  users/
    {user_slug}/
      profile.md              # identity, preferences, role, channel links (frontmatter + markdown)
      credentials.enc         # encrypted credentials blob
      conversation/           # continuous conversation storage
        {channel}/
          segments/
            seg-0001.jsonl    # sealed (summarized) segment
            seg-0002.jsonl    # active segment (append-only)
          summaries/
            seg-0001.summary.md  # rolling summary of seg-0001
          channel.meta.json   # channel-level metadata
          search_index.jsonl  # keyword search index
      cache/                  # SQLite caches for integrations
        banking.db            # banking transaction/balance cache
        news.db               # scraped news article cache
      memory/
        index.md              # one line per topic file: filename, one-liner (capped at 200 lines)
        calendars.md          # distilled facts about calendar preferences (with frontmatter)
        family.md             # family members, relationships, birthdays
      .pastes/                # large tool result content (SHA-256 hashed)
        {hash}                # content referenced by result_ref in history
    _household/               # shared family memories (included in all users' context)
      memory/
        wifi.md               # household wifi credentials
        address.md            # home address, emergency contacts
  artifacts/                  # rich content for Mini App (JSON files)
    {id}.json                 # artifact metadata + content
    files/                    # binary files (images, charts)
  watchdog/
    restart_requested         # flag file for self-restart
    restart_result            # restart outcome (ok, rolled_back, rollback_failed)
```

The data root defaults to `~/.marcel/`.  Set the `MARCEL_DATA_DIR`
environment variable to override at runtime, or patch
`marcel_core.storage._root._DATA_ROOT` in tests.

---

## File format specifications

### Continuous conversation (`conversation/{channel}/`)

Marcel uses a single continuous conversation per (user, channel) pair.
Conversations are stored as append-only JSONL segments with rolling
summaries:

```
conversation/
  telegram/
    segments/
      seg-0001.jsonl          # sealed segment (summarized)
      seg-0002.jsonl          # active segment (append-only)
    summaries/
      seg-0001.summary.md     # rolling summary with frontmatter
    channel.meta.json         # channel metadata
    search_index.jsonl        # keyword search index
```

Each JSONL line in a segment has the same format:

| Field | Type | Description |
|-------|------|-------------|
| `role` | string | `user`, `assistant`, `tool`, or `system` |
| `text` | string? | Message content (null if `result_ref` used) |
| `timestamp` | string | ISO 8601 UTC |
| `conversation_id` | string | Segment identifier |
| `tool_calls` | array? | For assistant: `[{id, name, arguments}]` |
| `tool_call_id` | string? | For tool results: matches a tool call ID |
| `tool_name` | string? | For tool results: which tool produced this |
| `result_ref` | string? | `sha256:{hash}` pointer to paste store |
| `is_error` | bool | Whether this tool result was an error |

#### Segment lifecycle

1. **Active**: New messages are appended to the active segment.
2. **Rotation**: When a segment reaches 500 messages or 500KB, a new
   segment file is created (file rotation, not summarization).
3. **Sealing**: When the conversation is idle for 60+ minutes, or the user
   sends `/forget`, the active segment is sealed and a new one opened.
4. **Summarization**: A Haiku agent generates a summary of the sealed
   segment, incorporating the previous summary (rolling chain). Tool
   results are stripped from sealed segments to save space.

#### Channel metadata (`channel.meta.json`)

```json
{
  "channel": "telegram",
  "created_at": "2026-04-10T09:00:00+00:00",
  "last_active": "2026-04-11T14:22:00+00:00",
  "active_segment": "seg-0002",
  "next_segment_num": 3,
  "total_messages": 247,
  "last_summary_at": "2026-04-11T10:15:00+00:00"
}
```

#### Segment summary (`summaries/seg-NNNN.summary.md`)

Summaries use YAML frontmatter with segment metadata, followed by the
summary text and optional key facts:

```markdown
---
segment_id: seg-0001
created_at: 2026-04-11T10:15:00+00:00
trigger: idle
message_count: 42
time_span_from: 2026-04-10T09:00:00+00:00
time_span_to: 2026-04-11T09:12:00+00:00
previous_summary_segment: null
---

The user discussed banking integration setup and configured...

## Key Facts
- User prefers afternoon appointments
- Banking sync configured for KBC account
```

#### Search index (`search_index.jsonl`)

Every user/assistant text message is keyword-indexed for mid-conversation
recall. Entries are compact JSONL:

```json
{"seg":"seg-0001","line":15,"ts":"2026-04-10T09:30:00","kw":["banking","kbc","setup"],"role":"user","preview":"Can you set up my KBC banking..."}
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
| `type` | yes | One of: `schedule`, `preference`, `person`, `reference`, `household`, `feedback` |
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
| `feedback` | Behavioral guidance from user corrections/confirmations | Permanent; injected into job agents |

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
    prune_expired_memories, rebuild_memory_index, enforce_index_cap,
    memory_age_days, memory_freshness_note, human_age,
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
Returns the markdown body of `profile.md` (without frontmatter), or an empty
string if the file does not exist.

```python
def save_user_profile(slug: str, content: str) -> None
```
Writes `content` as the body of `profile.md`, preserving existing frontmatter.
Creates the user directory if needed.

```python
def get_user_role(slug: str) -> str
def set_user_role(slug: str, role: str) -> None
```
Read/write the user's role (`'admin'` or `'user'`) from `profile.md` frontmatter.

---

### Conversations (continuous model)

```python
from marcel_core.memory.conversation import (
    ensure_channel, append_to_segment, read_active_segment,
    seal_active_segment, search_conversations,
    load_latest_summary, is_idle, has_active_content,
    ChannelMeta, SegmentSummary,
)
from marcel_core.memory.history import HistoryMessage, ToolCall
```

```python
def append_to_segment(user_slug: str, channel: str, message: HistoryMessage) -> ChannelMeta
```
Appends a message to the active segment, rotating if needed.

```python
def read_active_segment(user_slug: str, channel: str) -> list[HistoryMessage]
```
Reads all messages from the current active segment.

```python
def search_conversations(user_slug: str, channel: str, query: str, max_results: int = 5) -> list[tuple[SearchEntry, list[HistoryMessage]]]
```
Keyword search across conversation history with surrounding context.

```python
def load_latest_summary(user_slug: str, channel: str) -> SegmentSummary | None
```
Returns the most recent rolling summary for a channel.

### Summarization

```python
from marcel_core.memory.summarizer import summarize_if_idle, summarize_active_segment
```

```python
async def summarize_if_idle(user_slug: str, channel: str, idle_minutes: int = 60) -> bool
```
Checks if the channel is idle and triggers summarization if so. Called at
the start of each turn.

```python
async def summarize_active_segment(user_slug: str, channel: str, trigger: str = 'manual') -> bool
```
Seals the active segment, strips tool results, and generates a rolling
summary via Haiku.

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
def rebuild_memory_index(slug: str) -> None
```
Rebuilds `memory/index.md` from actual files on disk — removes entries for
deleted files, adds entries for new ones.

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
