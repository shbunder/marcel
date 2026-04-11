# ISSUE-055: System Prompt Optimization — Skill Index, Marcel Utils Tool, Channel Prompts

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, architecture, prompt-engineering

## Capture
**Original request:** "I just checked the Marcel system prompt and I would like to make a few crucial improvements. 1) All skills are being loaded into the main system prompt — this completely defeats the purpose of modular prompts that Marcel can read dynamically. 2) Tools available don't need to be in the system prompt, they are discovered through reading skills. 3) How to respond should be more focused on the current channel Marcel is communicating through — channel-specific prompts that explain capabilities and how to talk."

**Follow-up Q&A:**
- Q: For on-demand skill loading, how should the tool be structured?
- A: A single unified `marcel` utils tool that handles internal Marcel operations: reading memory, formatting responses, reading skills. External capability tools (browser, bash, etc.) stay separate. `notify` goes under the `marcel` tool too.

**Resolved intent:** Reduce system prompt bloat and improve modularity by (a) replacing full skill doc injection with a compact skill index + on-demand loading, (b) consolidating internal Marcel utilities into a single `marcel` tool, and (c) making the "how to respond" section channel-specific rather than a monolithic block covering all channels.

## Description

The current system prompt dumps the full content of every SKILL.md (~550 lines across 8 skills, growing) into every turn. This defeats the purpose of modular skills and wastes tokens on skill docs the agent may never need for a given conversation.

Three changes:

### 1. Skill index mode
Replace `format_skills_for_prompt()` with a compact index that lists each skill as one line: name + description from frontmatter. Full skill docs are loaded on-demand via the new `marcel` tool.

### 2. Unified `marcel` utils tool
Consolidate internal Marcel operations into a single `marcel` tool with action-based dispatch:

| Action | Replaces | Purpose |
|--------|----------|---------|
| `read_skill(name)` | Full skill injection in prompt | Load a skill's full docs on demand |
| `search_memory(query)` | `memory_search` tool | Search memory files |
| `search_conversations(query)` | `conversation_search` tool | Search conversation history |
| `compact()` | `compact_now` tool | Trigger conversation compaction |
| `notify(message)` | `notify` tool | Send progress update to user |

External capability tools remain separate: `browser`, `bash`, `read_file`, `write_file`, `edit_file`, `git_*`, `claude_code`, `generate_chart`, `integration`.

Tool tier architecture:
- **Internal (`marcel`)**: Marcel reading/managing its own state — skills, memory, conversations, notifications, compaction
- **Integration (`integration`)**: Calling external services through skill adapters
- **Capability (separate tools)**: Real external capabilities (browser, bash, file I/O, charts, etc.)

### 3. Channel-specific response prompts
Replace the monolithic "How to respond" section in MARCEL.md and the single-line `CHANNEL_FORMAT_HINTS` with per-channel prompt files at `<data_root>/channels/<channel>.md` (with defaults bundled in `src/marcel_core/defaults/channels/`). Only the active channel's file is injected into the system prompt.

Also remove the "Tools available" section from MARCEL.md — tools are self-describing via pydantic-ai schemas and the skill index.

## Tasks
- [ ] ISSUE-055-a: Design — detailed design for all three changes, confirm with user
- [ ] ISSUE-055-b: Implement skill index mode in `loader.py` + `context.py`
- [ ] ISSUE-055-c: Implement unified `marcel` utils tool (action-based dispatch)
- [ ] ISSUE-055-d: Migrate `memory_search`, `conversation_search`, `compact_now`, `notify` into `marcel` tool
- [ ] ISSUE-055-e: Remove old standalone tools, update tool registration in `agent.py`
- [ ] ISSUE-055-f: Implement channel-specific prompt files + loader
- [ ] ISSUE-055-g: Update MARCEL.md — remove "Tools available" and "How to respond" sections
- [ ] ISSUE-055-h: Update default skill docs that reference old tool names
- [ ] ISSUE-055-i: Tests for new tool dispatch, skill index, channel prompt loading
- [ ] ISSUE-055-j: Update docs (architecture.md, any references to old tools)

## Subtasks
- [ ] ISSUE-055-a: Design — detailed design document
- [ ] ISSUE-055-b: Skill index mode
- [ ] ISSUE-055-c: Marcel utils tool scaffold
- [ ] ISSUE-055-d: Migrate internal tools
- [ ] ISSUE-055-e: Remove old tools
- [ ] ISSUE-055-f: Channel prompt files
- [ ] ISSUE-055-g: Update MARCEL.md
- [ ] ISSUE-055-h: Update skill docs
- [ ] ISSUE-055-i: Tests
- [ ] ISSUE-055-j: Update docs

## Relationships
- Related to: [[ISSUE-033-marcel-md-system]] (MARCEL.md is being modified)

## Design

### Current system prompt structure (before)

```
┌─────────────────────────────────────────────┐
│ MARCEL.md (global)                          │  ~63 lines
│  ├─ Role                                    │
│  ├─ Tone and style                          │
│  ├─ Tools available          ← REMOVE       │  redundant with pydantic-ai schemas
│  ├─ How to respond           ← REPLACE      │  monolithic, covers all channels
│  └─ Handling unconfigured / Coding          │
├─────────────────────────────────────────────┤
│ User profile                                │  ~10 lines
├─────────────────────────────────────────────┤
│ Server context (admin only)                 │  ~8 lines
├─────────────────────────────────────────────┤
│ Available Skills                            │
│  ├─ banking    (124 lines)   ← FULL BODY   │
│  ├─ browser    (63 lines)                   │  ~550 lines total
│  ├─ developer  (68 lines)                   │  grows with each new skill
│  ├─ docker     (67 lines)                   │
│  ├─ icloud     (50 lines)                   │
│  ├─ jobs       (92 lines)                   │
│  ├─ memory     (34 lines)                   │
│  └─ settings   (54 lines)                   │
├─────────────────────────────────────────────┤
│ Memory (AI-selected, max 8)                 │  variable
├─────────────────────────────────────────────┤
│ Channel hint (1 sentence)                   │  ~2 lines
└─────────────────────────────────────────────┘
```

### Target system prompt structure (after)

```
┌─────────────────────────────────────────────┐
│ MARCEL.md (global) — trimmed                │  ~30 lines
│  ├─ Role                                    │
│  ├─ Tone and style                          │
│  └─ Handling unconfigured / Coding          │
├─────────────────────────────────────────────┤
│ User profile                                │  ~10 lines
├─────────────────────────────────────────────┤
│ Server context (admin only)                 │  ~8 lines
├─────────────────────────────────────────────┤
│ Skill Index (compact)                       │  ~12 lines (1 per skill)
│  ├─ banking — Belfius accounts, txns…       │
│  ├─ browser — Browse web, screenshots…      │  use marcel(read_skill=...) for full docs
│  └─ ...                                     │
├─────────────────────────────────────────────┤
│ Memory (AI-selected, max 8)                 │  variable
├─────────────────────────────────────────────┤
│ Channel prompt (full, from file)            │  ~20-40 lines
│  e.g. telegram.md with delivery modes,      │  replaces both CHANNEL_FORMAT_HINTS
│  notify guidance, mini app info, etc.       │  and MARCEL.md "How to respond"
└─────────────────────────────────────────────┘
```

**Estimated savings:** ~500 tokens per turn (8 skills × ~60 lines removed, +30 lines trimmed from MARCEL.md, −20 lines for channel prompt). Scales linearly — every new skill costs 1 index line instead of ~80 lines.

---

### Change 1: Skill index mode

**Current:** `format_skills_for_prompt()` in [loader.py:215-232](src/marcel_core/skills/loader.py#L215-L232) dumps full `SKILL.md` body for every skill.

**New:** Two functions replace `format_skills_for_prompt()`:

```python
def format_skill_index(skills: list[SkillDoc]) -> str:
    """One-line-per-skill index for the system prompt."""
    lines = []
    for skill in skills:
        status = " (not configured)" if skill.is_setup else ""
        lines.append(f"- **{skill.name}**{status} — {skill.description}")
    return '\n'.join(lines)

def get_skill_content(skill_name: str, user_slug: str) -> str:
    """Load full skill doc on demand (called by marcel tool)."""
    skills = load_skills(user_slug)
    for s in skills:
        if s.name == skill_name:
            return s.content
    return f"Unknown skill: {skill_name}"
```

**In context.py** `build_instructions_async()`: replace `format_skills_for_prompt(load_skills(...))` with `format_skill_index(load_skills(...))`. Add a brief instruction line:

```
## Skills
Use `marcel(action="read_skill", name="...")` to load full documentation before using an unfamiliar skill.

- **banking** — Access linked bank accounts — balances, transactions, spending insights
- **browser** — Browse the web — navigate, read, click, type, screenshots
- ...
```

---

### Change 2: Unified `marcel` utils tool

**New file:** `src/marcel_core/tools/marcel.py`

A single tool function with action-based dispatch. pydantic-ai sees one tool named `marcel` with a clear docstring describing all actions.

```python
async def marcel(
    ctx: RunContext[MarcelDeps],
    action: str,
    name: str | None = None,
    query: str | None = None,
    message: str | None = None,
    type_filter: str | None = None,
    max_results: int | None = None,
) -> str:
    """Marcel's internal utilities — use this to access skills, memory, and conversation history.

    Actions:
      read_skill    — Load full documentation for a skill (name= required)
      search_memory — Search memory files by keyword (query= required)
      search_conversations — Search past conversation history (query= required)
      compact       — Compress current conversation segment
      notify        — Send a progress update to the user (message= required)
    """
    match action:
        case "read_skill":
            ...  # calls loader.get_skill_content(name, ctx.deps.user_slug)
        case "search_memory":
            ...  # existing memory_search logic
        case "search_conversations":
            ...  # existing conversation_search logic
        case "compact":
            ...  # existing compact_now logic
        case "notify":
            ...  # existing notify logic
        case _:
            return f"Unknown action: {action}. Available: read_skill, search_memory, search_conversations, compact, notify"
```

**Why a single tool instead of keeping them separate:**
- Internal operations share the same conceptual tier — Marcel managing itself
- Reduces tool count in the API request (pydantic-ai sends full schemas for every tool)
- Matches the user's stated goal of a "single utils tool for internal working"
- External capabilities (browser, bash, file I/O, charts) stay as separate tools since they represent distinct, visible capabilities

**Migration in agent.py:**
```python
# Before:
agent.tool(integration_tools.memory_search)
agent.tool(integration_tools.conversation_search)
agent.tool(integration_tools.compact_now)
agent.tool(integration_tools.notify)

# After:
agent.tool(marcel_tool.marcel)
```

The old functions in `integration.py` (`memory_search`, `conversation_search`, `compact_now`, `notify`) are deleted. Their logic moves into `marcel.py` action handlers.

---

### Change 3: Channel-specific prompt files

**New directory structure:**

```
src/marcel_core/defaults/channels/
  cli.md
  app.md
  ios.md
  telegram.md
  websocket.md
  job.md

~/.marcel/channels/          ← seeded from defaults, user-editable
  cli.md
  app.md
  ...
```

Each file contains the full delivery guidance for that channel. Example `telegram.md`:

```markdown
---
name: telegram
---
You are responding via Telegram.

## Formatting
Use standard markdown (bold, italic, code, code blocks, links, lists, headers, blockquotes).
Do NOT use Telegram MarkdownV2 escape syntax — output will be converted server-side.

## Progress updates
For any task that takes more than one step, call `marcel(action="notify", message="...")` at the
start ("On it...") and after each major step. Never go silent for more than a few seconds.

## Delivery modes

### Default: plain text in the chat bubble
For most responses. This is the right choice 90% of the time.

### Visualizations
When data benefits from a visual — trends, comparisons, distributions — use `generate_chart`.
The chart is rendered server-side and sent as a photo. Do NOT describe charts in text.

### Interactive content: Mini App
Checklists (using `- [ ]` / `- [x]` markdown) get a "View in app" button for interaction.
```

**Loader** — new function in `context.py` (or a small `channels.py` module):

```python
def load_channel_prompt(channel: str) -> str:
    """Load channel-specific prompt from data root, falling back to defaults."""
    data_channel = settings.data_dir / 'channels' / f'{channel}.md'
    if data_channel.exists():
        _, body = _parse_frontmatter(data_channel.read_text())
        return body

    default = Path(__file__).parent.parent / 'defaults' / 'channels' / f'{channel}.md'
    if default.exists():
        _, body = _parse_frontmatter(default.read_text())
        return body

    return f'You are responding via the {channel} channel.'
```

**In context.py:** Replace the current 2-line channel hint:
```python
# Before:
format_hint = CHANNEL_FORMAT_HINTS.get(deps.channel, ...)
lines += ['## Channel', f'You are responding via the {deps.channel} channel. {format_hint}']

# After:
channel_prompt = load_channel_prompt(deps.channel)
lines += ['## Channel', channel_prompt]
```

`CHANNEL_FORMAT_HINTS` dict is deleted.

---

### Change 4: MARCEL.md cleanup

Remove from `~/.marcel/MARCEL.md`:
- **"## Tools available"** section (lines 22-29) — tools self-describe via pydantic-ai schemas
- **"## How to respond — delivery modes"** section (lines 30-55) — moved to channel files

Keep:
- Role, Tone and style, Handling unconfigured integrations, Coding and self-modification

Update the "Coding and self-modification" reference from "developer skill loaded into your context" to "developer skill (use `marcel(action="read_skill", name="developer")` to load full docs)".

Also update the defaults in `src/marcel_core/defaults/` to match.

---

### Tool tier summary (final state)

| Tier | Tool name | Registration | Who gets it |
|------|-----------|-------------|-------------|
| **Internal** | `marcel` | Always | All users |
| **Integration** | `integration` | Always | All users |
| **Capability** | `generate_chart` | Always | All users |
| **Capability** | `browser_*` (9 tools) | If playwright installed | All users |
| **Admin** | `bash`, `read_file`, `write_file`, `edit_file`, `git_*`, `claude_code` | If admin | Admin only |
| **Jobs** | `create_job`, `list_jobs`, etc. (9 tools) | Always | All users |

---

### Files changed

| File | Change |
|------|--------|
| `src/marcel_core/tools/marcel.py` | **NEW** — unified utils tool |
| `src/marcel_core/tools/integration.py` | Remove `memory_search`, `conversation_search`, `compact_now`, `notify` |
| `src/marcel_core/skills/loader.py` | Add `format_skill_index()`, `get_skill_content()`; keep `format_skills_for_prompt()` for backward compat or delete |
| `src/marcel_core/harness/context.py` | Use skill index; load channel prompt; delete `CHANNEL_FORMAT_HINTS` |
| `src/marcel_core/harness/agent.py` | Register `marcel` tool; remove 4 old tool registrations |
| `src/marcel_core/defaults/channels/*.md` | **NEW** — 6 channel prompt files |
| `src/marcel_core/defaults/skills/*/SKILL.md` | Update tool name references (`notify` → `marcel(action="notify", ...)`) |
| `~/.marcel/MARCEL.md` | Remove "Tools available" and "How to respond" sections |
| `src/marcel_core/defaults/MARCEL.md` | Same cleanup (bundled default) |
| `tests/` | New tests for marcel tool dispatch, skill index, channel loading |

### Risks and mitigations

**Risk: Model skips `read_skill` and calls `integration()` without full context.**

Two mitigations, inspired by clawcode's multi-layer approach:

1. **Prompt instruction (nudge)** — The skill index section includes an explicit instruction:
   "Before calling an integration for the first time, use `marcel(action="read_skill", name="...")` to load its full documentation."

2. **Auto-inject on first `integration()` call (safety net)** — When `integration(skill="banking.balance")` is called and the model hasn't previously read the `banking` skill in this conversation, the integration tool **prepends the skill docs to its response**. This is tracked per-conversation via a set on the deps/context object:

   ```python
   # In integration tool
   async def integration(ctx, skill, params=None):
       skill_family = skill.split('.')[0]
       read_skills: set = getattr(ctx, '_read_skills', set())
       prefix = ""
       if skill_family not in read_skills:
           content = get_skill_content(skill_family, ctx.deps.user_slug)
           if content:
               prefix = f"[Skill docs for {skill_family}]\n{content}\n\n---\n\n"
           read_skills.add(skill_family)
           ctx._read_skills = read_skills
       result = await run(config, params, ctx.deps.user_slug)
       return prefix + result
   ```

   The `read_skill` action in the `marcel` tool also adds to `_read_skills`, so if the model does read first, the integration call won't duplicate.

**Why this works (clawcode parallel):** Clawcode never relies on the model spontaneously discovering tools. It announces deferred tool names in `<system-reminder>` blocks (our skill index), provides ToolSearchTool always-loaded (our `marcel` tool), and tracks discovery state across turns (our `_read_skills` set). The auto-inject is our equivalent of clawcode's "discovered tools get full schemas on subsequent turns" — except we inject docs inline since we're dealing with knowledge, not tool schemas.

**Other risks:**
- **Single tool with many optional params** — pydantic-ai handles this fine; the action-based dispatch is a common pattern. The docstring clearly describes which params each action needs.
- **Channel file not found** — graceful fallback to a generic one-liner (same as current behavior).

## Comments

## Implementation Log
