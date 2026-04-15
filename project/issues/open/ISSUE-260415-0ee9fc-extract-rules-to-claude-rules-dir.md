# ISSUE-0ee9fc: Extract enforceable rules to .claude/rules/

**Status:** Open
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** High
**Labels:** tooling, workflow, docs

## Capture

**Original request:** extract-rules-to-claude-rules-dir — migrate Marcel's enforceable rules into .claude/rules/ using the path-scoped frontmatter pattern. Always-loaded: self-modification (request_restart only), git-staging (never git add .), closing-commit-purity (✅ close = pure status marker), docs-in-impl (docs ship in last 🔧 impl). Path-scoped: integration-pairs (skills/integrations/, defaults/skills/), data-boundaries (storage/, users/, auth/, config.py, memory/), role-gating (harness/, tools/, agents/). Trim GIT_CONVENTIONS and project/CLAUDE.md where content now lives in rules. Update pre-close-verifier to read .claude/rules/ as its enforcement source. Add a Rules section to docs/claude-code-setup.md.

**Follow-up Q&A:**
- Q: is `.claude/rules/` a real Claude Code CLI feature? A: Yes — user pasted the official docs. Rules live at `.claude/rules/*.md`, are loaded every session at the same priority as `.claude/CLAUDE.md`, and support path-scoping via `paths:` YAML frontmatter with glob patterns. Files without `paths:` are always loaded; files with `paths:` only load when Claude reads a matching file. Subdirectories are supported. Symlinks are supported.
- Q: do rules replace CLAUDE.md? A: No — they complement it. CLAUDE.md is for prose workflow and architectural context; rules are for short, enforceable, single-concept constraints that can be referenced from multiple places.

**Resolved intent:** Marcel's Core rules currently live scattered across `project/CLAUDE.md`, `project/issues/GIT_CONVENTIONS.md`, and implicit knowledge in subagents. Each file describes rules as prose bullets mixed with workflow explanation. This issue extracts the seven highest-value enforceable rules into `.claude/rules/` as single-concept files, using path-scoping where the rule only matters for a specific subtree. Four rules are always-loaded (workflow safety and git discipline), three are path-scoped (domain-specific: integrations, data boundaries, role gating). The pre-close-verifier subagent becomes rule-aware — it reads `.claude/rules/` as its enforcement source rather than hard-coding its checklist. Duplicated content in `GIT_CONVENTIONS.md` and `project/CLAUDE.md` is trimmed so the rule file is the single source of truth.

## Description

Follows ISSUE-999fa7 ([Claude Code setup hardening](./ISSUE-260415-999fa7-claude-code-setup-hardening.md)) which introduced subagents, the safety hook, and rotated lessons-learned but deliberately deferred the `.claude/rules/` question because the mechanism was unverified at the time. The user has now provided the official documentation: rules load at session start with the same priority as `.claude/CLAUDE.md`, and `paths:` frontmatter gates loading to specific file globs. This makes rules strictly better than CLAUDE.md for enforceable constraints because (a) they can be referenced individually by name, (b) path-scoped rules save context when irrelevant, and (c) the pre-close-verifier can enumerate them as its checklist source.

The seven candidates emerged from the ISSUE-999fa7 audit:

1. **self-modification.md** — `request_restart()` is the only legal restart path (always loaded).
2. **git-staging.md** — Never use `git add .` / `git add -A` (always loaded).
3. **closing-commit-purity.md** — `✅ close` commits contain only the status marker (always loaded).
4. **docs-in-impl.md** — Docs ship in the last `🔧 impl:` commit, never in `✅ close` and never in a fixup after merge (always loaded).
5. **integration-pairs.md** — Every integration is SKILL.md + SETUP.md + handler; half-shipped integrations leave the agent unable to onboard (path-scoped to `src/marcel_core/skills/**/*.py`, `src/marcel_core/defaults/skills/**/*`).
6. **data-boundaries.md** — User data in `~/.marcel/users/{slug}/`, system config in `.env`, never mix (path-scoped to `src/marcel_core/storage/`, `src/marcel_core/memory/`, `src/marcel_core/auth/`, `src/marcel_core/config.py`).
7. **role-gating.md** — Admin vs regular-user tool split; new tools must declare their tier (path-scoped to `src/marcel_core/harness/`, `src/marcel_core/tools/`, `src/marcel_core/agents/`).

## Tasks

- [ ] Create `.claude/rules/` directory structure
- [ ] Write `.claude/rules/self-modification.md` (always loaded) — references `request_restart()` path + bans direct `systemctl`/`docker restart`/`os.execv`
- [ ] Write `.claude/rules/git-staging.md` (always loaded) — bans `git add .` and `git add -A`, requires named staging
- [ ] Write `.claude/rules/closing-commit-purity.md` (always loaded) — enumerates exactly what `✅ close` may contain
- [ ] Write `.claude/rules/docs-in-impl.md` (always loaded) — docs ship in last `🔧 impl:` before close
- [ ] Write `.claude/rules/integration-pairs.md` with `paths: [src/marcel_core/skills/**/*.py, src/marcel_core/defaults/skills/**/*, tests/skills/**/*.py]`
- [ ] Write `.claude/rules/data-boundaries.md` with `paths: [src/marcel_core/storage/**/*.py, src/marcel_core/auth/**/*.py, src/marcel_core/config.py, src/marcel_core/memory/**/*.py]`
- [ ] Write `.claude/rules/role-gating.md` with `paths: [src/marcel_core/harness/**/*.py, src/marcel_core/tools/**/*.py, src/marcel_core/agents/**/*]`
- [ ] Trim `project/issues/GIT_CONVENTIONS.md` — remove duplicated "Staging rules" detail that now lives in `git-staging.md`; remove the closing-commit prose that now lives in `closing-commit-purity.md`; leave short references
- [ ] Trim `project/CLAUDE.md` — the "Core rules" section points at `.claude/rules/` for enforceable rules; keeps the workflow prose (create issue first, feature branches, make check, close, docs, user data)
- [ ] Update `.claude/agents/pre-close-verifier.md` — add a step that enumerates `.claude/rules/` as its enforcement source (along with the hardcoded checklist it already has)
- [ ] Update `docs/claude-code-setup.md` — add a "Rules" section explaining the layout, always-loaded vs path-scoped, how the verifier uses them, and how to add new rules
- [ ] Run `make check` — all green
- [ ] Close via `/finish-issue`

## Subtasks

(none — tasks are flat)

## Relationships

- Depends on: [[ISSUE-260415-999fa7-claude-code-setup-hardening]] — introduced the subagents and docs that this issue extends. The pre-close-verifier in particular needs the rules infrastructure to graduate from a hardcoded checklist to a rule-driven one.

## Comments

### 2026-04-15 - LLM
Decision rationale on path scoping: always-loaded rules cover universal workflow safety (git discipline, close-commit purity, self-modification). Anything that only matters when touching a specific part of the code (integrations, storage/auth/config, harness/tools) is path-scoped so the always-loaded footprint stays minimal. The risk is that a rule scoped to `src/marcel_core/storage/` won't fire when someone edits a test that violates the boundary from `tests/` — acceptable trade-off because tests are discovered when Claude reads them anyway and the relevant source files will be touched in the same session.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
