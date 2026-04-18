# ISSUE-bde0a1: Migrate pure-markdown skill habitats to the zoo

**Status:** Open
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

- [ ] Audit [defaults/skills/](../../src/marcel_core/defaults/skills/) and classify each skill: has integration handler (moves in ISSUE-2ccc10) vs pure-markdown (moves here).
- [ ] For each pure-markdown skill: move the entire directory from `src/marcel_core/defaults/skills/<name>/` to `~/.marcel/skills/<name>/`.
- [ ] Verify SKILL.md frontmatter still parses without `depends_on:` (loader must handle both the `depends_on` and the `requires`-only shapes per ISSUE-6ad5c7).
- [ ] If any skill resources (e.g. `feeds.yaml`-style files) accompany a pure-markdown skill, they travel with it.
- [ ] Delete `src/marcel_core/defaults/skills/<name>/` for each migrated skill (per-skill-directory deletion, not bulk — `defaults/` as a whole goes away in ISSUE-63a946).
- [ ] Keep a minimal `defaults/` seeding path alive for any skill that still lives there (e.g. if we haven't yet migrated it). The full deletion is ISSUE-63a946's job.
- [ ] Tests: where a pure-markdown skill has tests (rare — most are doc-only), move them to the habitat's `tests/` directory.
- [ ] Docs: update [docs/skills.md](../../docs/skills.md) to clarify the three skill shapes — integration-backed (`depends_on`), pure-markdown with `requires:`, pure-markdown with no requirements.

## Relationships

- Depends on: ISSUE-6ad5c7 (skill habitat layout)
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
