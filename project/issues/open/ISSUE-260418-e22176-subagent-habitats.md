# ISSUE-e22176: Subagent habitats (fifth habitat)

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Subagent definitions (...) Are they a fifth habitat (`zoo/agents/`), or do they belong inside the habitat that uses them?" → User confirmed: **fifth habitat**.

**Resolved intent:** Subagents are the fifth marcel-zoo habitat type alongside integrations, skills, channels, and jobs. Unlike integrations and channels, they're reusable across habitats — a news summarizer subagent might be invoked from a job, an integration, and a skill — so they earn their own top-level directory rather than being folded into whichever habitat happens to invoke them first.

## Description

Today subagent definitions live at [src/marcel_core/defaults/agents/](../../src/marcel_core/defaults/agents/) and are seeded into `~/.marcel/agents/` on first boot. The loader at [agents/loader.py](../../src/marcel_core/agents/loader.py) already reads from `<data_root>/agents/` — runtime-side, subagents are almost already a habitat.

This issue does two things:

1. **Stop seeding** subagents from `defaults/agents/`. Move the files from the source tree into `~/.marcel/agents/` as the authoritative location. Runtime behavior unchanged (loader already reads data root), but the kernel no longer ships example agents.
2. **Document the agent habitat pattern.** Each subagent is a single markdown file today (`.md` with frontmatter per Claude Code's subagent format). The habitat convention here is "one markdown file = one subagent" — no wrapper directory unless the subagent grows resources (prompt fragments, reference data). If/when that happens, promote to `zoo/agents/<name>/agent.md` + resources. For now, files-at-the-root is fine.

Question to resolve during implementation: do agents need a plugin API surface at all? They don't execute Python code — they're pure prompts loaded by the kernel's agent runner. So no `marcel_core.plugin.agents` is needed; they're just markdown files loaded from a conventional path. This is the simplest habitat type.

Note: this issue moves **runtime subagents** (explore, default agent definitions under `defaults/agents/`). It does **not** touch [.claude/agents/](../../.claude/agents/) — those are Claude Code developer-mode agents (`pre-close-verifier`, `code-reviewer`, `security-auditor`) and live inside the repo as part of the dev toolchain. They're a different thing with the same name; leave them alone.

## Tasks

- [ ] Audit [src/marcel_core/defaults/agents/](../../src/marcel_core/defaults/agents/) — list every file + what it does.
- [ ] For each file: move from `src/marcel_core/defaults/agents/<name>.md` → `~/.marcel/agents/<name>.md`.
- [ ] Remove the `_DEFAULTS_DIR / 'agents'` seeding block from [src/marcel_core/defaults/__init__.py:94-104](../../src/marcel_core/defaults/__init__.py).
- [ ] Verify: fresh boot with empty `~/.marcel/agents/` does not spawn a default — user has to install marcel-zoo (or hand-drop an agent file).
- [ ] Verify: [agents/loader.py](../../src/marcel_core/agents/loader.py) still discovers files from the data-root path (should be no change — it already reads there).
- [ ] Tests: any tests that loaded subagents from `defaults/agents/` update to either (a) a fake agent fixture under a tmp data root or (b) point at the migrated zoo file. Kernel loader tests must not depend on real agent content.
- [ ] Docs: new section in `docs/plugins.md` (or `docs/subagents.md`) describing the agent habitat pattern — one markdown file at `<data_root>/agents/<name>.md`, frontmatter per Claude Code subagent format, promoted to a directory if resources are needed.

## Relationships

- Depends on: ISSUE-3c87dd (for consistency with other habitat pattern documentation; technically could ship earlier — no plugin-API coupling)
- Blocks: ISSUE-63a946 (zoo repo extraction)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
