# ISSUE-033: MARCEL.md System + Orphaned Docs Cleanup

**Status:** Open
**Created:** 2026-04-09
**Assignee:** Claude
**Priority:** High
**Labels:** feature, refactor

## Capture
**Original request:**
1. "why are the src/marcel_core/skills/docs skills still there? shouldn't they be moved?" — orphaned skill docs from ISSUE-032 were not deleted
2. "can we create MARCEL.md files, similar to CLAUDE.md files that are read in the same way claude code would do. The goal is to separate normal behaviour where I use Marcel as an agent, and the situation where Marcel uses claude code to create something (or reprogram his own harness)"

**Resolved intent:** Two changes. First, delete the now-orphaned `src/marcel_core/skills/docs/` directory (skills moved to `.marcel/skills/` in ISSUE-032 but the source wasn't removed). Second, create a MARCEL.md file system inspired by Claude Code's CLAUDE.md loading: Marcel reads `.marcel/MARCEL.md` (project-level) and `~/.marcel/MARCEL.md` (user home) and injects them into the system prompt. This cleanly separates personal-assistant instructions (MARCEL.md) from developer/code-change instructions (CLAUDE.md, read by the inner Claude Code loop). The personal assistant guidance currently mixed into `CLAUDE.md` moves to `.marcel/MARCEL.md`.

## Tasks
- [ ] Delete `src/marcel_core/skills/docs/` (orphaned after ISSUE-032)
- [ ] Create `src/marcel_core/agent/marcelmd.py` — MARCEL.md loader (home + upward CWD walk)
- [ ] Create `.marcel/MARCEL.md` — project-level personal assistant instructions
- [ ] Update `build_system_prompt()` to inject MARCEL.md content
- [ ] Trim `CLAUDE.md` to developer-only (personal assistant sections move to `.marcel/MARCEL.md`)
- [ ] Write tests for the loader
- [ ] Update docs

## Implementation Log
