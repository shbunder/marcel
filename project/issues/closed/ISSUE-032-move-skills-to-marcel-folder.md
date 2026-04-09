# ISSUE-032: Move Skills to .marcel/ Folder

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Claude
**Priority:** High
**Labels:** feature, refactor

## Capture
**Original request:** "I want to move marcel skills to .marcel folder in this repo, basically when reading skills the same system as claude code should be followed (you can get inspired by ~/repos/clawcode for skill management): 1) skills should be read from the current working directory .marcel and from the home folder .marcel. This should also work in our docker setup (so it should read the .marcel folder here, and the one in the home-folder, docker should have access to it) 2) each integration skill should have backup technique. if the user doesn't have the integration configured, a backup skill should be called instead (or be active) that explains how to setup the integration for that new user."

**Resolved intent:** Migrate Marcel's skill documentation (SKILL.md files) from `src/marcel_core/skills/docs/` and `.claude/skills/` to a new `.marcel/skills/` directory, following Claude Code's multi-location skill discovery pattern. Skills are loaded from both the project directory (`.marcel/skills/`) and the user's home directory (`~/.marcel/skills/`), with project-level skills taking precedence. Each integration skill gets a SETUP.md fallback that activates when the integration isn't configured, guiding new users through setup.

## Description

### Current state
- Integration skill docs live in `src/marcel_core/skills/docs/{name}/SKILL.md`
- `install_skills.py` symlinks/copies them to `.claude/skills/` for Claude Code auto-discovery
- Workflow skills (new-issue, finish-issue) live in `.claude/skills/` and are for developer use
- `skills.json` has shell skill configs (plex.restart)

### Target state
- Integration skill docs move to `.marcel/skills/{name}/SKILL.md`
- New loader reads from CWD `.marcel/skills/` and `~/.marcel/skills/` (home wins for user customizations, CWD provides defaults)
- Each integration skill has a `SETUP.md` fallback for unconfigured integrations
- Skill docs are injected into the system prompt by Marcel's own loader (not relying on Claude Code's `.claude/skills/` discovery)
- Docker setup already mounts both paths; just needs Dockerfile update
- Developer workflow skills (new-issue, finish-issue) stay in `.claude/skills/`

## Tasks
- [✓] Create `.marcel/skills/` directory with SKILL.md and SETUP.md files
- [✓] Create `src/marcel_core/skills/loader.py` (multi-dir discovery + fallback logic)
- [✓] Add requirement checks via SKILL.md frontmatter `requires` field
- [✓] Update `context.py` to inject loaded skill docs into system prompt
- [✓] Update `install_skills.py` to target `.marcel/skills/`
- [✓] Update Dockerfile to copy `.marcel/skills/`
- [✓] Update tests (26 new tests for loader)
- [✓] Clean up old locations (`.claude/skills/icloud` symlink, docs references)
- [✓] Update all CLAUDE.md, docs, and lesson-learned references

## Implementation Log

### 2026-04-09 — LLM Implementation
**Action**: Full skill migration from .claude/skills/ to .marcel/skills/
**Files Created**:
- `.marcel/skills/icloud/SKILL.md` — iCloud skill doc with `requires` frontmatter
- `.marcel/skills/icloud/SETUP.md` — Setup guide for unconfigured iCloud
- `.marcel/skills/banking/SKILL.md` — Banking skill doc with `requires` frontmatter
- `.marcel/skills/banking/SETUP.md` — Setup guide for unconfigured banking
- `.marcel/skills/plex/SKILL.md` — Plex skill doc
- `.marcel/skills/plex/SETUP.md` — Setup guide for unconfigured Plex
- `src/marcel_core/skills/loader.py` — Multi-directory skill discovery with fallback logic
- `tests/core/test_skill_loader.py` — 26 tests covering parsing, requirements, loading, merging

**Files Modified**:
- `src/marcel_core/agent/context.py` — Inject skill docs into system prompt via `_load_skills()`
- `src/marcel_core/skills/__init__.py` — Export `load_skills`
- `src/marcel_core/skills/install_skills.py` — Target `.marcel/skills/` instead of `.claude/skills/`
- `src/marcel_core/tools/integration.py` — Update docstring reference
- `Dockerfile` — Copy `.marcel/skills/` instead of running install_skills
- `Makefile` — Update install-skills comment
- `.gitignore` — Remove old `.claude/skills/icloud/` and `.claude/skills/banking/` entries
- `CLAUDE.md` — Update skill location references
- `project/CLAUDE.md` — Rewrite integration pattern section
- `project/issues/CLAUDE.md` — Update skill directory reference
- `project/lessons-learned.md` — Update pattern reference
- `docs/skills.md` — Full rewrite with new architecture and SETUP.md docs
- `docs/architecture.md` — Update directory tree
- `docs/integration-banking.md` — Update skill doc path reference
- `.claude/skills/finish-issue/SKILL.md` — Add `.marcel/skills/` to grep check

**Files Removed**:
- `.claude/skills/icloud` symlink

**Commands Run**: `uv run pytest tests/core/ -v` — 292 passed
**Result**: Success — all tests passing
