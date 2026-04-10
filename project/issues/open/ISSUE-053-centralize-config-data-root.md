# ISSUE-053: Centralize Config to Data Root

**Status:** Open
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature, config, architecture

## Capture
**Original request:** "Centralize config — move .marcel/ from repo to data root, wire up MARCEL.md and skills in system prompt"

**Follow-up Q&A:** User wants `~/.marcel/` to be the single config directory Marcel actively reads from. The repo should not contain `.marcel/` at all — defaults are bundled in the package and seeded on first startup. Additionally, MARCEL.md and skills were loaded by infrastructure that was never wired into the system prompt — this must be activated.

**Resolved intent:** Move all runtime config (MARCEL.md, skills/) out of the repository's `.marcel/` directory into the data root (`~/.marcel/`). Bundle defaults in `src/marcel_core/defaults/` for seeding on first startup. Wire up the existing but unused MARCEL.md loader and skills loader into the system prompt builder so Marcel actually knows about its personality, tools, response modes, and available integrations. Add response delivery guidance (when to use notify, generate_chart, plain text) to MARCEL.md.

## Description

Marcel's config was split: `.marcel/` in the repo held MARCEL.md and skills, while `~/.marcel/` held user data. This caused two problems:

1. **Split config** — confusing to manage, especially in Docker where the data root is a mounted volume but repo files are baked into the image.
2. **Unused infrastructure** — `load_marcelmd_files()` and `load_skills()` existed and were tested but never called in the system prompt builder. Marcel literally didn't know about its own skills, personality, or response capabilities.

This issue centralizes everything under the data root and activates the prompt injection.

## Tasks
- [ ] Copy `.marcel/` contents (MARCEL.md + skills/) to `~/.marcel/`
- [ ] Update MARCEL.md with response delivery guidance (notify, generate_chart, plain text)
- [ ] Create `src/marcel_core/defaults/` with bundled seed files
- [ ] Update `marcelmd.py` — remove project path lookup, only use data_root
- [ ] Update `skills/loader.py` — remove project path lookup, only use data_root
- [ ] Wire `load_marcelmd_files()` + `load_skills()` into `build_instructions_async()` in context.py
- [ ] Add `seed_defaults()` to main.py lifespan (copy defaults if not present)
- [ ] Remove `.marcel/` from repo, add to `.gitignore`
- [ ] Update CLAUDE.md, project/CLAUDE.md, project/issues/CLAUDE.md references
- [ ] Verify: tests pass (682)

## Relationships
- Related to: [[ISSUE-033-marcel-md-system]] (originally created the MARCEL.md loading infrastructure)
- Related to: [[ISSUE-032-move-skills-to-marcel-folder]] (originally moved skills to .marcel/)
- Related to: [[ISSUE-052-rich-content-delivery]] (generate_chart documented in response delivery guidance)

## Comments

## Implementation Log
