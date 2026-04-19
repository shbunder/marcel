# Marcel Developer Guide

This file governs coder mode — when Marcel is being extended, debugged, or rewriting its own code. For a definition of when coder mode applies, see [CLAUDE.md](../CLAUDE.md).

The **how** is as important as the **what**. A working feature that breaks the architecture or makes the next change harder is not a good outcome.

## Workflow rules (always apply)

- **Create an issue first.** Anything beyond a one-line typo needs an issue. Use `/new-issue` — it handles branch creation, hash-ID allocation, and the `📝` commit.
- **Work on feature branches.** Never commit implementation work directly to `main`. Each issue gets `issue/{hash}-{slug}`.
- **`make check` must pass before closing.** Format, lint, typecheck, and tests with coverage all green. A pre-commit hook enforces this.
- **Never leave an issue in `wip/` at the end of a conversation.** Close it with `/finish-issue` or explicitly tell the user why it remains open.

## Enforceable rules (in .claude/rules/)

Short, single-concept rules with enforcement by the subagents live under [.claude/rules/](../.claude/rules/). Loaded every session. Path-scoped rules only load when Claude is reading matching files.

- [git-staging](../.claude/rules/git-staging.md) — never `git add .`; always stage by name
- [closing-commit-purity](../.claude/rules/closing-commit-purity.md) — `✅ close` commits are pure status markers
- [docs-in-impl](../.claude/rules/docs-in-impl.md) — docs ship in the last `🔧 impl:` commit before close
- [self-modification](../.claude/rules/self-modification.md) — `request_restart()` is the only legal restart path
- [debugging](../.claude/rules/debugging.md) — Reproduce → Localize → Reduce → Fix → Guard; no guess-and-check
- [data-boundaries](../.claude/rules/data-boundaries.md) — user data in `~/.marcel/users/{slug}/`, system config in `.env`, never mix *(path-scoped)*
- [integration-pairs](../.claude/rules/integration-pairs.md) — integrations ship as handler + `SKILL.md` + `SETUP.md`, never half *(path-scoped)*
- [role-gating](../.claude/rules/role-gating.md) — admin vs non-admin tool split, enforced structurally at harness startup *(path-scoped)*

## Detailed references (load on demand)

- [FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) — the 8-step procedure (capture, requirements, issue, design, scaffold, tests, implement, ship)
- [CODING_STANDARDS.md](./CODING_STANDARDS.md) — Marcel-specific rules ruff/mypy don't cover (API design, type system, coverage policy)
- [issues/CLAUDE.md](./issues/CLAUDE.md) — issue lifecycle, directory layout, anti-rationalization
- [issues/TEMPLATE.md](./issues/TEMPLATE.md) — the issue markdown template
- [issues/GIT_CONVENTIONS.md](./issues/GIT_CONVENTIONS.md) — commit sequence, staging rules, merging, fixups
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

1. **Create an issue first** — `/new-issue`, then work on the feature branch as usual. No shortcuts because the request came through chat.
2. **Follow the full feature development procedure.**
3. **Respond via Telegram when done** — after merging, send the user a Telegram message containing the `git log --oneline -1` output (commit hash + message) and a brief summary from the Implementation Log. Use `marcel(action="notify", message="...")` or the Telegram bot directly. The user should not need to check git to know what happened.

This rule exists so that all work is traceable, project history is readable, and the user always knows what changed in response to their request.
