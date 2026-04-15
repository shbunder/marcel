# ISSUE-0ee9fc: Extract enforceable rules to .claude/rules/

**Status:** Closed
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

- [✓] Create `.claude/rules/` directory structure
- [✓] Write `.claude/rules/self-modification.md` (always loaded) — references `request_restart()` path + bans direct `systemctl`/`docker restart`/`os.execv`
- [✓] Write `.claude/rules/git-staging.md` (always loaded) — bans `git add .` and `git add -A`, requires named staging
- [✓] Write `.claude/rules/closing-commit-purity.md` (always loaded) — enumerates exactly what `✅ close` may contain
- [✓] Write `.claude/rules/docs-in-impl.md` (always loaded) — docs ship in last `🔧 impl:` before close
- [✓] Write `.claude/rules/integration-pairs.md` with `paths: [src/marcel_core/skills/**/*.py, src/marcel_core/defaults/skills/**/*, tests/skills/**/*.py]`
- [✓] Write `.claude/rules/data-boundaries.md` with `paths: [src/marcel_core/storage/**/*.py, src/marcel_core/auth/**/*.py, src/marcel_core/config.py, src/marcel_core/memory/**/*.py, src/marcel_core/channels/**/*.py]`
- [✓] Write `.claude/rules/role-gating.md` with `paths: [src/marcel_core/harness/**/*.py, src/marcel_core/tools/**/*.py, src/marcel_core/agents/**/*]`
- [✓] Trim `project/issues/GIT_CONVENTIONS.md` — replaced the prose "Staging rules" detail with links to `git-staging.md` and `closing-commit-purity.md`; kept the per-emoji quick reference
- [✓] Trim `project/CLAUDE.md` — split "Core rules" into "Workflow rules" (keep) and "Enforceable rules (in .claude/rules/)" (links to all 7 rule files)
- [✓] Update `.claude/agents/pre-close-verifier.md` — added Step 2a which enumerates `.claude/rules/` at runtime; always-loaded rules read unconditionally; path-scoped rules read only when the diff matches their globs
- [✓] Update `docs/claude-code-setup.md` — added "Rules" section explaining always-loaded vs path-scoped, how subagents enumerate rules, and the 4-step process for adding a new rule
- [✓] Run `make check` — all green on every commit (1344 tests, 92.75% coverage, pre-commit hook enforced)
- [✓] Close via `/finish-issue`

## Subtasks

(none — tasks are flat)

## Relationships

- Depends on: [[ISSUE-260415-999fa7-claude-code-setup-hardening]] — introduced the subagents and docs that this issue extends. The pre-close-verifier in particular needs the rules infrastructure to graduate from a hardcoded checklist to a rule-driven one.

## Comments

### 2026-04-15 - LLM
Decision rationale on path scoping: always-loaded rules cover universal workflow safety (git discipline, close-commit purity, self-modification). Anything that only matters when touching a specific part of the code (integrations, storage/auth/config, harness/tools) is path-scoped so the always-loaded footprint stays minimal. The risk is that a rule scoped to `src/marcel_core/storage/` won't fire when someone edits a test that violates the boundary from `tests/` — acceptable trade-off because tests are discovered when Claude reads them anyway and the relevant source files will be touched in the same session.

## Implementation Log

### 2026-04-15 19:30 - LLM Implementation
**Action**: Shipped 7 rule files under `.claude/rules/` with YAML-frontmatter path scoping where applicable, wired the `pre-close-verifier` subagent to read them at runtime, and trimmed duplicated prose from `GIT_CONVENTIONS.md` + `project/CLAUDE.md`.

**Files Modified**:
- `.claude/rules/self-modification.md` (new, always-loaded)
- `.claude/rules/git-staging.md` (new, always-loaded)
- `.claude/rules/closing-commit-purity.md` (new, always-loaded)
- `.claude/rules/docs-in-impl.md` (new, always-loaded)
- `.claude/rules/integration-pairs.md` (new, path-scoped to `skills/` and `defaults/skills/`)
- `.claude/rules/data-boundaries.md` (new, path-scoped to `storage/`, `auth/`, `config.py`, `memory/`, `channels/`)
- `.claude/rules/role-gating.md` (new, path-scoped to `harness/`, `tools/`, `agents/`)
- `.claude/agents/pre-close-verifier.md` — added Step 2a (enumerate rules at runtime)
- `project/issues/GIT_CONVENTIONS.md` — trimmed "Staging rules" section, link to rules
- `project/CLAUDE.md` — split "Core rules" into "Workflow rules" + "Enforceable rules (in .claude/rules/)"
- `docs/claude-code-setup.md` — new "Rules" section
- `project/plans/architecture-overview.md` — marked Superseded (the historical doc contained `git add -A` prescriptions that predate the current flow)
- `.claude/skills/new-issue/SKILL.md`, `.claude/skills/finish-issue/SKILL.md` — kept inline "stage by name" reminders but now link to `git-staging.md` rule

**Commands Run**:
- `grep -rn -E '(git add \.|git add -A|git commit -a)' --include='*.md' .` — final straggler grep
- `grep -n "Enumerate applicable rules" .claude/agents/pre-close-verifier.md` — confirmed Step 2a added
- `head -10` on each path-scoped rule to confirm YAML frontmatter is valid
- `make check` — passed after every commit via pre-commit hook (1344 tests, 92.75% coverage)

**Result**: All 14 tasks complete. Seven rule files in place with correct frontmatter. Docs updated. Verifier rule-aware. Trim left no dangling references to old prose.

**Reflection** (inline — the `pre-close-verifier` subagent file exists but confirmed empirically that subagents only load at session start, not mid-session. The `Agent(subagent_type="pre-close-verifier", ...)` call returned "Agent type not found" with only the built-in agents listed. Fell back to the inline checklist per the skill's fallback instructions):
- **Verdict:** APPROVE
- **Coverage:** 14/14 tasks addressed
- **Shortcuts found:** none — the rule files have no TODOs, no bare `except`, no magic numbers, no half-written sections. The verifier Step 2a addition is well-scoped (instructs the subagent to enumerate rules; no hardcoded expectations the subagent would need to know at compile time).
- **Scope drift:** none — every change maps to a task. The `architecture-overview.md` superseded-note was unplanned but was caught by the straggler grep and is a direct enforcement of the new `git-staging` rule (the doc was prescribing the rule's forbidden pattern), so updating it was in-scope.
- **Stragglers:** two `git add -A` lines remain in `project/plans/architecture-overview.md` at lines 329 and 331. These are inside the historical "Self-Modification + Git Rollback" flow description, which predates the current `request_restart()` + `redeploy.sh` mechanism. Rewriting the lines would misrepresent history; instead, the doc header was updated to **Superseded** with an inline link to the git-staging rule flagging the violation so any future reader understands they are historical, not prescriptive. Documented exception.
- **Marcel-specific checks:** No `git mv` after a Read. No restart-bypass added. No user-data/system-config cross-contamination. All path-scoped rules have syntactically valid YAML frontmatter. Subagent files unchanged except for pre-close-verifier's Step 2a addition.

**Next**: The next issue will be the first real test of `.claude/rules/` loading and of the `pre-close-verifier` subagent being invokable. Both live by name at this point — they will either work in a fresh session or not, and we will find out on the next `/new-issue` + `/finish-issue` cycle.
