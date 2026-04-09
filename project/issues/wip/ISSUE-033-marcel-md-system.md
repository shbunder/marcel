# ISSUE-033: MARCEL.md System + Orphaned Docs Cleanup

**Status:** WIP
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
- [✓] Delete `src/marcel_core/skills/docs/` (orphaned after ISSUE-032)
- [✓] Create `src/marcel_core/agent/marcelmd.py` — MARCEL.md loader (home + upward CWD walk)
- [✓] Create `.marcel/MARCEL.md` — project-level personal assistant instructions
- [✓] Update `build_system_prompt()` to inject MARCEL.md content
- [✓] Trim `CLAUDE.md` to developer-only (personal assistant sections move to `.marcel/MARCEL.md`)
- [✓] Write tests for the loader (11 tests)
- [✓] Update docs

## Implementation Log

### 2026-04-09 — LLM Implementation
**Action**: MARCEL.md system + orphaned docs cleanup
**Files Created**:
- `src/marcel_core/agent/marcelmd.py` — MARCEL.md loader: discovers files from home + upward CWD walk, deduplicates by resolved path, concatenates in priority order
- `.marcel/MARCEL.md` — project-level personal assistant instructions (persona, tone, tools)
- `tests/core/test_marcelmd.py` — 11 tests for loader discovery, ordering, dedup, empty-file handling

**Files Modified**:
- `src/marcel_core/agent/context.py` — inject MARCEL.md via `_load_marcelmd()`, add no-MARCEL.md fallback identity line
- `src/marcel_core/skills/install_skills.py` — converted to no-op (skills now live directly in .marcel/skills/)
- `CLAUDE.md` — rewritten as developer-only; personal assistant content moved to .marcel/MARCEL.md
- `docs/architecture.md` — updated module layout to include marcelmd.py and .marcel/MARCEL.md

**Files Deleted**:
- `src/marcel_core/skills/docs/icloud/SKILL.md` (orphaned, now in .marcel/skills/)
- `src/marcel_core/skills/docs/banking/SKILL.md` (orphaned, now in .marcel/skills/)

**Commands Run**: `uv run pytest tests/core/ -q` → 303 passed
**Result**: Success — all tests passing
