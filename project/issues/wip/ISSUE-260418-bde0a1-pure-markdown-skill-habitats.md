# ISSUE-bde0a1: Migrate pure-markdown skill habitats to the zoo

**Status:** WIP
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "There are indeed skills that depend on integrations (and we can make that explicit) but they should stay cleanly separated." — implying some skills have NO integration dependency. Those are the skills this issue moves.

**Resolved intent:** Several skills in [defaults/skills/](../../src/marcel_core/defaults/skills/) are pure teaching material — no Python handlers, no external services. They teach the agent how to use **built-in kernel tools** (`web`, `memory`, `developer`, `jobs`, `ui`). They have no `depends_on`. This issue moves them into the zoo as skill-only habitats, confirming the habitat pattern supports the "pure teaching" case cleanly.

## Description

Current pure-markdown skills (no integration, no Python handler):

- `web` — teaches the agent to use the web fetch / browse tool
- `memory` — teaches `marcel(action="search_memory")` usage
- `developer` — coder-mode teaching (likely admin-only)
- `jobs` — teaches job scheduling via the kernel `jobs` tool
- `ui` — teaches A2UI component usage
- (audit at implementation time — the list above is from a quick scan of [defaults/skills/](../../src/marcel_core/defaults/skills/))

Each moves to `~/.marcel/skills/<name>/` with SKILL.md, optional SETUP.md, optional components.yaml. No `depends_on:` field. Any `requires:` stays in SKILL.md frontmatter (e.g. `web` may need `CHROMIUM_PATH` — the skill declares it directly because there's no integration layer to own it).

The skill loader keeps its current fallback logic for these: in-frontmatter `requires:` drives SKILL.md → SETUP.md switching. Per ISSUE-6ad5c7 the loader also handles `depends_on:` for integration-backed skills; both paths coexist.

No code changes — purely a content move. Small issue by comparison with the integration migrations.

## Tasks

- [✓] Audit [defaults/skills/](../../src/marcel_core/defaults/skills/) and classify each skill: has integration handler (moves in ISSUE-2ccc10) vs pure-markdown (moves here). Six qualify: `developer`, `jobs`, `memory`, `settings`, `ui`, `web`. None had `depends_on:`; none had a paired integration handler.
- [✓] For each pure-markdown skill: move the entire directory from `src/marcel_core/defaults/skills/<name>/` to marcel-zoo's `skills/<name>/`.
- [✓] Verify SKILL.md frontmatter still parses without `depends_on:` (loader must handle both the `depends_on` and the `requires`-only shapes per ISSUE-6ad5c7). Confirmed — `_normalize_depends_on` returns `[]` for absent field, `_check_depends_on([], ...)` returns `True`.
- [✓] If any skill resources (e.g. `feeds.yaml`-style files) accompany a pure-markdown skill, they travel with it (ui's `components.yaml` moved with it).
- [✓] Delete `src/marcel_core/defaults/skills/<name>/` for each migrated skill. With all six gone, `src/marcel_core/defaults/skills/` ceased to exist entirely.
- [✓] Keep a minimal `defaults/` seeding path alive for any skill that still lives there — refactored `seed_defaults()` to guard the skill-seeding block with `if src_skills.is_dir():` instead of the function-level early return, so channel/routing/agents seeding still runs even when `defaults/skills/` vanishes.
- [✓] Tests: no pure-markdown skill had its own tests — nothing to move. The seeder tests in `tests/core/test_defaults.py` and loader tests in `tests/core/test_skills.py` use fake `defaults_dir` fixtures and stay green.
- [✓] Docs: update [docs/skills.md](../../docs/skills.md) to name the three skill shapes explicitly — standalone (no requirements), self-contained (inline `requires:`), integration-backed (`depends_on:`). Also updated README.md, docs/web.md, docs/subagents.md, and `.claude/agents/code-reviewer.md` to reflect that the kernel no longer ships bundled default skills.

## Relationships

- Depends on: ISSUE-6ad5c7 (skill habitat layout)
- Blocks: ISSUE-63a946 (zoo repo extraction)

## Implementation Log

### 2026-04-19 — Migration complete

All six pure-markdown skills (`developer`, `jobs`, `memory`, `settings`, `ui`, `web`) moved from kernel `src/marcel_core/defaults/skills/` to marcel-zoo's `skills/`. No `depends_on:` needed because none of them front a Python integration handler — they teach the agent how to use built-in kernel tools (the `marcel(action=...)` utility, the `web` god-tool, the A2UI component catalog).

**Kernel-side changes (this branch):**
- Deleted `src/marcel_core/defaults/skills/{developer,jobs,memory,settings,ui,web}/` in full. With that, `defaults/skills/` no longer exists.
- Refactored `src/marcel_core/defaults/__init__.py::seed_defaults` to guard the skill-seeding block with `if src_skills.is_dir():` instead of a function-level early return. The early return would have skipped channel, routing, and agent seeding when `defaults/skills/` disappeared — a silent regression waiting to happen.
- Updated [docs/skills.md](../../docs/skills.md) with a new "three skill shapes" section (standalone / self-contained / integration-backed).
- Updated [README.md](../../README.md) to stop instructing contributors to drop new skills into the kernel's `defaults/skills/` — they go into marcel-zoo now.
- Updated [docs/web.md](../../docs/web.md) and [docs/subagents.md](../../docs/subagents.md) to fix stale path references.
- Updated [.claude/agents/code-reviewer.md](../../.claude/agents/code-reviewer.md) to reflect that the kernel ships zero default skills.

**Zoo-side changes (marcel-zoo@edbb33e):**
- Added `skills/{developer,jobs,memory,settings,ui,web}/` — 8 files, 506 insertions.
- Committed independently on marcel-zoo's `main` as "add pure-markdown skill habitats (migrated from kernel ISSUE-bde0a1)".

**Not touched:** `src/marcel_core/skills/integrations/` (still houses zero integrations, per ISSUE-2ccc10). Loader itself unchanged — the existing `_normalize_depends_on` and `_check_depends_on` logic already handles the "no depends_on" case correctly.

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
