# Marcel Developer Guide

This file governs coder mode — when Marcel is being extended, debugged, or rewriting its own code. For a definition of when coder mode applies, see [CLAUDE.md](../CLAUDE.md).

The **how** is as important as the **what**. A working feature that breaks the architecture or makes the next change harder is not a good outcome.

## Workflow rules (always apply)

Work is tracked on the **marcel-admin board** (`$MARCEL_ADMIN_DIR`, default `~/projects/marcel-admin`) as **Feature → Story → Subtask**; code is implemented here on a per-feature branch. Full conventions: `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`.

- **Create a feature first.** Anything beyond a one-line typo needs a feature on the board. Use `/new-feature` — it allocates `FEAT-…`, scaffolds the requirements page, and prints the `feat/FEAT-…-slug` branch. Break it into stories with `/new-story`; record decisions with `/new-adr`.
- **Work on feature branches.** Never commit implementation work directly to `main`. Each feature gets `feat/FEAT-…-slug`; code commits are prefixed `[STORY-…]`.
- **Keep the two repos separate.** Code + SDK-doc commits live here; ticket/status/requirements/ADR edits are committed in `marcel-admin`. Never mix the two.
- **`make check` must pass before merging.** Format, lint, typecheck, and tests with coverage all green. A pre-commit hook enforces this.
- **Never leave a feature's branch unmerged at the end of a conversation.** Close it with `/finish-feature` or explicitly tell the user why it remains open.

## Enforceable rules (in .claude/rules/)

Short, single-concept rules with enforcement by the subagents live under [.claude/rules/](../.claude/rules/). Loaded every session. Path-scoped rules only load when Claude is reading matching files.

- [git-staging](../.claude/rules/git-staging.md) — never `git add .`; always stage by name
- [closing-commit-purity](../.claude/rules/closing-commit-purity.md) — code commits and board (ticket/status) commits never mix; they live in different repos
- [docs-in-impl](../.claude/rules/docs-in-impl.md) — requirements/ADRs live on the board and ship with the feature; SDK docs ship with the code change
- [self-modification](../.claude/rules/self-modification.md) — `request_restart()` is the only legal restart path
- [debugging](../.claude/rules/debugging.md) — Reproduce → Localize → Reduce → Fix → Guard; no guess-and-check
- [data-boundaries](../.claude/rules/data-boundaries.md) — user data in `~/.marcel/users/{slug}/`, system config in `.env`, never mix *(path-scoped)*
- [integration-pairs](../.claude/rules/integration-pairs.md) — integrations ship as handler + `SKILL.md` + `SETUP.md`, never half *(path-scoped)*
- [role-gating](../.claude/rules/role-gating.md) — admin vs non-admin tool split, enforced structurally at harness startup *(path-scoped)*

## Detailed references (load on demand)

- [FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) — the 8-step procedure (capture, requirements, feature, design, scaffold, tests, implement, ship)
- [CODING_STANDARDS.md](./CODING_STANDARDS.md) — Marcel-specific rules ruff/mypy don't cover (API design, type system, coverage policy)
- [issues/CLAUDE.md](./issues/CLAUDE.md) — where issue tracking went (the marcel-admin board) + the frozen `closed/` archive
- `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md` — the Feature/Story/Subtask model, the `jira.py` CLI, and the cross-repo git workflow (board side)
- [VERSIONING.md](./VERSIONING.md) — version bump policy

## Philosophy

Core principles are defined in [CLAUDE.md](../CLAUDE.md). All development work must follow them: lightweight over bloated, generic over specific, human-readable over clever, recoverable over fast.

## Self-modification safety

When rewriting Marcel's own code:

- **Commit before restarting.** Every change must be recoverable via `git revert`.
- **Always trigger restart via `request_restart()`** — never `systemctl restart` or `docker restart` directly. The flag-based mechanism provides the rollback safety net. See [FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) for the restart recipe.
- **Confirm with the user before restarting** unless they explicitly asked for auto-restart.
- **Restricted files.** Auth logic, core config, and safety rules (including CLAUDE.md files) are off-limits unless the user explicitly grants permission for a specific change. When in doubt, ask.

## Integration pattern (summary)

New integrations ship as a pair of habitats in marcel-zoo (or any checkout pointed to by `MARCEL_ZOO_DIR`):

1. **Create the integration habitat** at `<MARCEL_ZOO_DIR>/integrations/<name>/__init__.py`. Use `@register("name.action")` from `marcel_core.plugin` to register async handlers. Each handler receives `(params: dict, user_slug: str)` and returns a string. Add an `integration.yaml` alongside declaring `provides:` (the handler IDs) and `requires:` (credentials, env vars, files, packages).
2. **Create the skill habitat** at `<MARCEL_ZOO_DIR>/skills/<name>/SKILL.md` with `depends_on: [<name>]` in the frontmatter. Teaches the agent how to call `integration(id="name.action", params={...})` with inline examples.
3. **Create a setup fallback** at `<MARCEL_ZOO_DIR>/skills/<name>/SETUP.md`. Shown when the integration's requirements are not met — the agent walks the user through providing them.
4. **For simple HTTP/shell integrations**, add a JSON entry to `skills.json` instead — no Python module needed. Still create the paired skill habitat with `SKILL.md` and `SETUP.md`.

Skill habitats are discovered at startup from `<MARCEL_ZOO_DIR>/skills/` (zoo) and `<data_root>/skills/` (`~/.marcel/skills/` — user customizations, data root wins on collision). The kernel itself ships zero bundled skills. The loader in `src/marcel_core/skills/loader.py` reads from both sources and injects docs into the system prompt.

Integrations must be self-contained — they should not require changes to core Marcel code (`tool.py`, `executor.py`, `runner.py`). Verify the pattern works end-to-end before committing. Full habitat contract: [docs/plugins.md](../docs/plugins.md).

## Telegram-initiated changes

When a user requests a code change via Telegram:

1. **Create a feature first** — `/new-feature`, then work on the feature branch as usual. No shortcuts because the request came through chat.
2. **Follow the full feature development procedure.**
3. **Respond via Telegram when done** — after merging, send the user a Telegram message containing the `git log --oneline -1` output (commit hash + message) and a brief summary from the Implementation Log. Use `marcel(action="notify", message="...")` or the Telegram bot directly. The user should not need to check git to know what happened.

This rule exists so that all work is traceable, project history is readable, and the user always knows what changed in response to their request.
