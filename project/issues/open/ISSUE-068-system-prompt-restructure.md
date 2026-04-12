# ISSUE-068: System Prompt Restructure — Clean H1 Blocks + Dynamic Memory

**Status:** Open
**Created:** 2026-04-12
**Assignee:** LLM
**Priority:** High
**Labels:** feature, refactor, prompt-engineering

## Capture

**Original request (verbatim):**

> looking at this event log I see a few things that worry me:
> 1) a lot of memory is read in from the start in the system prompt. I thought that would be a bit more dynamic and allow Marcel to dynamically search memory. Only the basics should be there
> 2) the segment summary is very good
> 3) I don't understand how skills are read in, I see marcel call for a skill but then only a small part of the text is loaded? Am I missing something?
> 4) overall the system prompt / system instructions looks very messy I would expect cleaner Level 1 headers of the type:
> \# Marcel: who are you?
> \# Shaun: who is the user?
> \# Telegram: what channel are we on?
> \# Memory: What should you know?
>
> Can you investigate these topics and see where we need improvements?

**Follow-up Q&A:**

- *Q: Include `# Skills` as a fifth H1 block?* → **A: Yes.** User: "agreed with # Skills (What can you do?)".
- *Q: Are skills being truncated when Marcel calls `read_skill`?* → **No.** Investigation confirmed that [skills.py:30](src/marcel_core/tools/marcel/skills.py#L30) returns the full `SKILL.md` body (e.g. banking = 5569 bytes on disk). The truncation visible in `event-log.md` is from the OpenInference span processor ([tracing.py:28](src/marcel_core/tracing.py#L28)) serializing the tool result into an OTel span attribute for Phoenix — the model receives the full string, only the trace viewer is misleading. The cosmetic fix (bump `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` or drop the OpenInference processor) is out of scope for this issue unless it turns out to be trivial while touching `tracing.py`.
- *Q: Scope?* → **A: "make one big issue for this, be thorough"**. Combine restructure + dynamic memory into a single issue.

**Resolved intent:**

Rewrite `build_instructions_async` so the system prompt is assembled from five clearly-separated H1 blocks (`# Marcel`, `# Shaun`, `# Skills`, `# Memory`, `# Telegram`), each containing only its own concern. Replace the current "AI-pre-selected memory dump" with a compact memory *index* (same pattern already used for skills) and add a `read_memory` action to the `marcel` tool so Marcel can pull full memory content on demand via `search_memory` or `read_memory`. Clean up the content inside each block (strip duplicate H1s from loaded files at load time, drop redundant preambles, normalize sub-heading levels) so the final prompt reads like a coherent document instead of five concatenated fragments.

## Description

### Problem

A fresh read of `event-log.md` (a Phoenix export from a real Telegram turn) surfaces four issues with the current system prompt:

1. **Memory is front-loaded, not dynamic.** [context.py:188-199](src/marcel_core/harness/context.py#L188-L199) calls `select_relevant_memories()` and pastes the full body of every selected memory file into `## Memory`. Because [selector.py:49](src/marcel_core/memory/selector.py#L49) defines `SELECTION_THRESHOLD = 10` and the user currently has ≤10 memory files, *every memory is loaded every turn* — the "AI selector" never runs. Meanwhile, `marcel(action="search_memory", query=…)` already exists as a tool ([memory.py:15](src/marcel_core/tools/marcel/memory.py#L15)), it's just never needed because everything is pre-loaded. This wastes context on memories that are irrelevant to the current turn and prevents the model from learning to reach for memory search on its own.

2. **`read_skill` appears truncated in traces — but isn't.** Verified: the Phoenix event log shows `read_skill` returning ~200 chars cut off mid-word, but [skills.py:30](src/marcel_core/tools/marcel/skills.py#L30) returns the full file body unchanged, and `banking/SKILL.md` is 5569 bytes on disk. The model sees the full doc. The trace viewer is lying because of attribute-length limits in the OpenInference processor / OTLP exporter. **No runtime fix needed**; note this in `lessons-learned.md` so it doesn't get investigated again.

3. **H1 structure is messy.** Current order in [context.py:203-234](src/marcel_core/harness/context.py#L203-L234):
   - `MARCEL.md` block (contains its own `# Marcel — Personal Assistant Instructions` H1 internally)
   - `## What you know about shaun` (H2)
   - `## Server Context (Admin)` (H2, admin only)
   - `## Skills` (H2)
   - `## Memory` (H2)
   - `## Channel` (H2)

   Heading levels are inconsistent (the Marcel block is H1, everything else is H2), user identity is buried in `## What you know about…` instead of being a top-level block, and the channel block opens with a redundant `You are responding via Telegram.` line.

4. **Content inside blocks has cruft.** Global `MARCEL.md` starts with a self-referential blockquote ("This file provides global rules for all users. Per-user instructions live at…") that's developer documentation, not something the model needs. `profile.md` has its own `# Shaun` H1 that collides with the intended `# Shaun` wrapper. `telegram.md` has an unnecessary `You are responding via Telegram.` opener that duplicates what the `# Telegram` H1 will say.

### Solution

#### A. Five-block prompt assembly

Rewrite `build_instructions_async` to produce exactly this structure (separator = blank line between H1 blocks):

```
# Marcel — who you are
  <body of global MARCEL.md, leading H1 stripped, blockquote stripped>
  ## Role / ## Tone and style / ## Handling unconfigured integrations / ## Coding and self-modification

# Shaun — who the user is
  <body of profile.md, leading H1 stripped>
  ## About / ## Preferences / ## Known facts
  ## Server context        (admin only, folded in here as H2)

# Skills — what you can do
  <compact index, one line per skill>

  *Full docs are loaded on demand — call `marcel(action="read_skill", name="…")`.*

# Memory — what you should know
  <compact index, one line per memory file>

  *Search with `marcel(action="search_memory", query="…")` or load a specific file with `marcel(action="read_memory", name="…")`.*

# Telegram — how to respond
  <body of telegram.md, leading `You are responding via Telegram.` stripped>
  ## Formatting / ## Progress updates / ## Delivery modes / ## What NOT to do
```

The H1 titles are picked to read as questions the model is answering: *who you are, who the user is, what you can do, what you should know, how to respond.*

#### B. Dynamic memory (the real context win)

1. **Drop `select_relevant_memories` from the prompt builder.** The function stays in the repo for now (it's still used by the job executor — verify with grep), but the interactive prompt path no longer calls it.
2. **Add `format_memory_index(headers)`** alongside [storage/memory.py](src/marcel_core/storage/memory.py)'s existing `format_memory_manifest`. Output: one line per memory, `- **name** — description` with an optional ` _(stale: Nd)_` marker when `memory_freshness_note` returns non-empty. Mirrors `format_skill_index` in shape.
3. **Add a `read_memory` action** to the `marcel` tool, next to `search_memory` and `save_memory` in [tools/marcel/memory.py](src/marcel_core/tools/marcel/memory.py). Signature: `read_memory(ctx, name)` → returns full file body with `[type] name (age)` label. Wire it into [dispatcher.py](src/marcel_core/tools/marcel/dispatcher.py). Update the `marcel` tool's docstring/action list so the model knows the action exists.
4. **Update `~/.marcel/skills/memory/SKILL.md`** (and the bundled default at `src/marcel_core/defaults/skills/memory/SKILL.md`) to document the `read_memory` action alongside `search_memory` and `save_memory`.

#### C. Content cleanups (via load-time stripping, not file edits)

Per ISSUE-067's lesson: **don't edit data-root copies of files that will drift from bundled defaults**. Instead, strip the noise at load time so user-editable files keep their natural structure.

1. **`_strip_leading_h1(body)` helper** — add to [marcelmd.py](src/marcel_core/harness/marcelmd.py) (or a shared `harness/_markdown.py` if it makes sense). Removes a leading `# Heading\n` line and any trailing blank lines, so both `MARCEL.md` and `profile.md` can have their own H1 on disk (natural markdown) but get wrapped under our chosen H1 in the prompt.
2. **`_strip_self_ref_blockquote(body)` helper** — strips a leading `> …` blockquote paragraph from `MARCEL.md`. Scoped check: only strip if it mentions the phrase "per-user instructions" or "this file" (dev-doc tell).
3. **`_strip_channel_preamble(body)`** — for `telegram.md`, drop a leading line matching `You are responding via the \w+ channel\.` or `You are responding via \w+\.`. Again, generic and content-driven so the user can keep the file readable on disk.

All three strippers are small, pure functions with unit tests — no edits to the `.md` files themselves. This also means the cleanup survives a `seed_defaults` refresh.

#### D. Skills hint placement

Current [context.py:222-224](src/marcel_core/harness/context.py#L222-L224) puts the "load full docs via `read_skill`" hint *before* the index, which reads oddly. Move it *after* and rephrase as: *"Full docs are loaded on demand — call `marcel(action="read_skill", name="…")`."*. Apply the same pattern (index → hint) to the new Memory block.

### Out of scope

- **Phoenix trace truncation fix** — the model gets full content, only the trace viewer is misleading. Document in lessons-learned and move on. (If the fix turns out to be a one-line env var bump while touching `tracing.py` for another reason, it's a trivial add-on, not a goal.)
- **Deleting `select_relevant_memories`** — keep it for now; the job executor may still use it, and the safe migration is "stop calling from prompt builder" + "delete in a follow-up once we confirm no other callers."
- **Restructuring `MARCEL.md` on disk** — do it via load-time stripping, not file edits.

## Tasks

### Phase 1 — Helpers & tests (Steps 5-6 of dev procedure)

- [ ] ISSUE-068-a: Add `_strip_leading_h1`, `_strip_self_ref_blockquote`, `_strip_channel_preamble` helpers with unit tests in `tests/harness/test_prompt_cleanup.py`
- [ ] ISSUE-068-b: Add `format_memory_index(headers: list[MemoryHeader]) -> str` in `storage/memory.py` with unit test
- [ ] ISSUE-068-c: Write unit tests for the new `build_instructions_async` structure: assert the output contains exactly the expected five H1 blocks in order, no duplicate H1s, and admin-only server context folds under `# Shaun`

### Phase 2 — Dynamic memory plumbing

- [ ] ISSUE-068-d: Add `read_memory` action to `tools/marcel/memory.py` (signature: `async def read_memory(ctx, name)`, returns full file body with type/name/age label)
- [ ] ISSUE-068-e: Wire `read_memory` into `tools/marcel/dispatcher.py` action map
- [ ] ISSUE-068-f: Update the `marcel` tool docstring / action catalog so the model sees `read_memory` as an available action
- [ ] ISSUE-068-g: Unit test for `read_memory` — happy path, unknown-name path, uses `turn` state if applicable

### Phase 3 — Prompt builder rewrite

- [ ] ISSUE-068-h: Rewrite `build_instructions_async` in `harness/context.py` to emit the five-block structure using the new helpers. Keep the function signature unchanged so callers in `runner.py` don't break
- [ ] ISSUE-068-i: Remove `select_relevant_memories` call and the `## Memory` dump from `build_instructions_async`; replace with `format_memory_index` + hint line
- [ ] ISSUE-068-j: Fold `build_server_context` output under the `# Shaun` H1 as an H2 (`## Server context`) instead of emitting a separate top-level block
- [ ] ISSUE-068-k: Update `build_instructions` (the sync fallback at `context.py:239`) to use the same five-block structure — don't let the two diverge

### Phase 4 — Skill doc updates

- [ ] ISSUE-068-l: Update `src/marcel_core/defaults/skills/memory/SKILL.md` to document `read_memory` alongside `search_memory` and `save_memory`
- [ ] ISSUE-068-m: Verify (don't edit) that `~/.marcel/skills/memory/SKILL.md` picks up the update via `seed_defaults` — if it's already present, note the drift in a follow-up task instead of silently overwriting

### Phase 5 — Verification & ship

- [ ] ISSUE-068-n: Run `make check` — format, lint, typecheck, tests with coverage. Fix any fallout
- [ ] ISSUE-068-o: Smoke test: start Marcel, send a query via CLI, capture the rendered system prompt (log at DEBUG level or inspect via Phoenix), confirm the five-block structure appears and memory index replaces the dump
- [ ] ISSUE-068-p: Smoke test: ask Marcel a question that should trigger a memory lookup ("what do you know about my family?"), confirm it calls `search_memory` or `read_memory` instead of relying on pre-loaded content
- [ ] ISSUE-068-q: Grep `docs/` for any references to the old prompt structure (`## What you know about`, `## Memory`, `## Channel` section names, `select_relevant_memories`) and update `docs/architecture.md` + `docs/prompts.md` (if it exists) to reflect the new H1 layout
- [ ] ISSUE-068-r: Append implementation log + lesson ("Phoenix trace viewer truncates tool results — don't investigate length mismatches there, inspect the model message stream instead") to `project/lessons-learned.md`
- [ ] ISSUE-068-s: Closing commit — move issue to `closed/`, bump version, push to `shaun` branch, `request_restart()`

## Relationships

- **Related to:** [[ISSUE-058-memory-system]] — this replaces the front-loaded selector with dynamic loading; `select_relevant_memories` stays as the job-executor path but is no longer the prompt path
- **Related to:** [[ISSUE-067-a2ui-rendering]] — ISSUE-067 added `format_components_catalog` alongside `format_skill_index`; the same "load skills once, pass to multiple formatters" pattern should be preserved in the rewrite
- **Inspired by:** [[ISSUE-066-post-065-audit]] — the "clean module boundaries" mindset from the post-audit cleanup carries over to "clean prompt structure"

## Code touch points (reference map)

| File | Change |
|------|--------|
| [harness/context.py:157-236](src/marcel_core/harness/context.py#L157-L236) | Rewrite `build_instructions_async` — five H1 blocks |
| [harness/context.py:239-271](src/marcel_core/harness/context.py#L239-L271) | Rewrite `build_instructions` to match |
| [harness/context.py:81-118](src/marcel_core/harness/context.py#L81-L118) | `build_server_context` — change top-level header from `## Server Context (Admin)` to `## Server context` (folded under `# Shaun`) |
| [harness/marcelmd.py](src/harness/marcelmd.py) | Add `_strip_leading_h1` + `_strip_self_ref_blockquote` helpers; apply in `format_marcelmd_for_prompt` |
| [harness/context.py:121-154](src/marcel_core/harness/context.py#L121-L154) | `load_channel_prompt` — apply `_strip_channel_preamble` |
| [storage/memory.py](src/marcel_core/storage/memory.py) | Add `format_memory_index(headers) -> str` |
| [tools/marcel/memory.py](src/marcel_core/tools/marcel/memory.py) | Add `read_memory(ctx, name)` action |
| [tools/marcel/dispatcher.py](src/marcel_core/tools/marcel/dispatcher.py) | Wire `read_memory` into action map + action list in docstring |
| [tools/marcel/__init__.py](src/marcel_core/tools/marcel/__init__.py) | Re-export `read_memory` if needed for symmetry |
| [defaults/skills/memory/SKILL.md](src/marcel_core/defaults/skills/memory/SKILL.md) | Document `read_memory` action |
| [tests/harness/test_prompt_cleanup.py](tests/harness/test_prompt_cleanup.py) | New file — unit tests for strippers + index formatters + block structure |
| [tests/tools/test_marcel_memory.py](tests/tools/test_marcel_memory.py) | Extend with `read_memory` happy/error paths |
| [docs/architecture.md](docs/architecture.md) | Update prompt-assembly section to describe the five H1 blocks |
| [project/lessons-learned.md](project/lessons-learned.md) | Append ISSUE-068 lesson: Phoenix trace truncation ≠ runtime issue; memory index pattern |

## Design notes

### Why strip H1s at load time instead of editing the files?

Three reasons, in order of importance:

1. **ISSUE-067 taught us** that data-root copies (`~/.marcel/MARCEL.md`, `~/.marcel/channels/telegram.md`) diverge silently from bundled defaults because `seed_defaults` never overwrites. If we edit the files, only *my* copy gets fixed — every other user who already has a data root stays broken.
2. **Markdown files should be readable on disk**. A `profile.md` that starts with content instead of its own `# Shaun` heading is unnatural for the user to edit by hand.
3. **The loader is the right place for presentation decisions**. The files store *content*; the prompt builder decides how to frame it.

### Why keep `select_relevant_memories` instead of deleting it?

It's still imported in the job executor path (needs verification in task 068-a via grep). Deleting it in the same commit risks breaking background jobs silently. Cleaner migration: stop calling from prompt builder now, delete the function in a follow-up once we confirm no other callers.

### Why `read_memory` when we already have `search_memory`?

- `search_memory` returns snippets matching a keyword — good for "do we have anything about X?"
- `read_memory` loads a specific file by name — good for "I see `family` in the memory index, give me the whole thing"

Same distinction as `read_skill` vs. "try this skill and see what happens." The index + on-demand read pattern only works if there's a direct way to pull a specific entry after seeing its name in the index.

### What does the new Memory block actually look like in the prompt?

```markdown
# Memory — what you should know

- **family** — Family members, kids, partner Cosette, shared schedule
- **work** — Work shifts, employer, salary, fire brigade involvement
- **travel** — Travel history and upcoming trips
- **credentials_policy** — Policy for credential storage — user creds in /data, never in memory
- **apple_integration** — Apple iCloud integration — mail, calendar, notes via pyicloud
- **data_storage_preferences** — User data goes in data/users/{slug}/, system config in .env
- **calendars** — iCloud calendar setup, calendar names, and recurring schedule items
- **home_infrastructure** — Home server (NUC), Docker services, Plex, networking setup
- **communication_apps** _(stale: 2d)_ — Communication apps and platforms Shaun uses
- **grocery_shopping** _(stale: 8d)_ — Regular grocery shopping locations and habits

*Search with `marcel(action="search_memory", query="…")` or load a specific file with `marcel(action="read_memory", name="…")`.*
```

Compared to the current dump, that's ~12 lines instead of ~150 — a ~90% reduction on the Memory section alone, with zero loss of discoverability.

## Comments

### 2026-04-12 - Shaun
"make one big issue for this, be thorough"

### 2026-04-12 - LLM
Investigation confirmed:
- Memory is loaded every turn via the `len(headers) <= SELECTION_THRESHOLD` branch (selector never runs for current memory count)
- `read_skill` truncation is a Phoenix display bug, not a runtime bug — model receives full content
- `## Server Context (Admin)`, `## Skills`, `## Memory`, `## Channel` are all H2 while the MARCEL.md block is H1 → inconsistent nesting
- `search_memory` tool already exists but is never called because everything is pre-loaded

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
