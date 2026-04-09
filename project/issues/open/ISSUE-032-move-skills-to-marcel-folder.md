# ISSUE-032: Move Skills to .marcel/ Folder

**Status:** Open
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
- [ ] Create `.marcel/skills/` directory with SKILL.md and SETUP.md files
- [ ] Create `src/marcel_core/skills/loader.py` (multi-dir discovery + fallback logic)
- [ ] Add `is_available()` checks to integration modules
- [ ] Update `context.py` to inject loaded skill docs into system prompt
- [ ] Update `install_skills.py` to target `.marcel/skills/`
- [ ] Update Dockerfile to copy `.marcel/skills/`
- [ ] Update tests
- [ ] Clean up old locations (`src/marcel_core/skills/docs/`, `.claude/skills/` integration symlinks)

## Implementation Log
