# ISSUE-055: System Prompt Optimization — Skill Index, Marcel Utils Tool, Channel Prompts

**Status:** Open
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

## Comments

## Implementation Log
