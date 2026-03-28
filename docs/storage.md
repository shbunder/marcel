# Storage

Marcel stores all user data as plain-text markdown files — no database required.
The storage module lives at `src/marcel_core/storage/` and provides a
synchronous Python API for reading and writing users, conversations, and memory.

---

## Data directory layout

```
data/
  users/
    {user_slug}/
      profile.md              # display name, preferences, known facts (free-form markdown)
      channel_ids.json        # {"cli": "fingerprint", "telegram": "12345"}
      conversations/
        index.md              # one line per conversation: date, filename, short description
        2026-03-26T14-32.md   # full turn-by-turn transcript
        2026-03-25T09-11.md
      memory/
        index.md              # one line per topic file: filename, one-liner
        calendar.md           # distilled facts about calendar preferences
        family.md             # family members, relationships, birthdays
        shopping.md           # shopping habits, preferred stores
```

The data root defaults to `{repo_root}/data`.  Set the `MARCEL_DATA_DIR`
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

Free-form prose and bullet lists.  The file is overwritten in full each time.

```markdown
# Calendar Preferences

Shaun prefers dentist appointments in the afternoon.
Shaun's team lunch is recurring every Thursday.
Work calendar: primary Google Calendar account.
```

### Memory index (`memory/index.md`)

One line per topic file.  `update_memory_index` appends a line only if the
topic is not already present.

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
    user_exists, load_user_profile, save_user_profile,
    new_conversation, append_turn, load_conversation,
    load_conversation_index, update_conversation_index,
    load_memory_index, load_memory_file, save_memory_file, update_memory_index,
    get_lock,
)
```

---

### Users

```python
def user_exists(slug: str) -> bool
```
Returns `True` if `data/users/{slug}/` exists.

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

## Atomicity

Every write goes through `_atomic.atomic_write(path, content)`:

1. Write to a temp file in the same directory (via `tempfile.mkstemp`).
2. `os.rename(tmp, path)` — atomic on POSIX systems.
3. On error: delete the temp file and re-raise.

This guarantees that readers never see a partially-written file.
