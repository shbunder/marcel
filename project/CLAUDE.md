# Marcel Developer Guide

This file governs coder mode — when Marcel is being extended, debugged, or rewriting its own code. For a definition of when coder mode applies, see [CLAUDE.md](../CLAUDE.md).

The **how** is as important as the **what**. A working feature that breaks the architecture or makes the next change harder is not a good outcome.

## Core rules (always apply)

- **Create an issue first.** Anything beyond a one-line typo needs an issue. Use `/new-issue` — it handles branch creation, hash-ID allocation, and the `📝` commit.
- **Work on feature branches.** Never commit implementation work directly to `main`. Each issue gets `issue/{hash}-{slug}`.
- **`make check` must pass before closing.** Format, lint, typecheck, and tests with coverage all green. A pre-commit hook enforces this.
- **Never leave an issue in `wip/` at the end of a conversation.** Close it with `/finish-issue` or explicitly tell the user why it remains open.
- **Document in the same change as the code.** New feature → new or updated page in `docs/` per [docs/CLAUDE.md](../docs/CLAUDE.md).
- **User data in `~/.marcel/users/{slug}/`, system config in `.env`.** Never mix. See `docs/storage.md` for the storage API.

## Detailed references (load on demand)

- [FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) — the 8-step procedure (capture, requirements, issue, design, scaffold, tests, implement, ship)
- [CODING_STANDARDS.md](./CODING_STANDARDS.md) — style, API design, type system, error handling
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

New integrations follow this pattern:

1. **Create a python integration module** at `src/marcel_core/skills/integrations/<name>.py`. Use `@register("name.action")` to register async handlers. Each handler receives `(params: dict, user_slug: str)` and returns a string.
2. **Create a skill doc** at `<data_root>/skills/<name>/SKILL.md` (and the default in `src/marcel_core/defaults/skills/<name>/`). Teaches the agent how to call `integration(id="name.action", params={...})` with inline examples. Add a `requires` field listing credentials, env vars, or files needed.
3. **Create a setup fallback** at `<data_root>/skills/<name>/SETUP.md`. Shown when the skill's requirements are not met.
4. **For simple HTTP/shell integrations**, add a JSON entry to `skills.json` instead — no Python module needed. Still create SKILL.md and SETUP.md.

Skills live at `<data_root>/skills/` (`~/.marcel/skills/`). Default skills are bundled in `src/marcel_core/defaults/skills/` and seeded on first startup. The loader in `skills/loader.py` reads from the data root and injects docs into the system prompt.

Integrations must be self-contained — they should not require changes to core Marcel code (`tool.py`, `executor.py`, `runner.py`). Verify the pattern works end-to-end before committing.

## Telegram-initiated changes

When a user requests a code change via Telegram:

1. **Create an issue first** — `/new-issue`, then work on the feature branch as usual. No shortcuts because the request came through chat.
2. **Follow the full feature development procedure.**
3. **Respond via Telegram when done** — after merging, send the user a Telegram message containing the `git log --oneline -1` output (commit hash + message) and a brief summary from the Implementation Log. Use `marcel(action="notify", message="...")` or the Telegram bot directly. The user should not need to check git to know what happened.

This rule exists so that all work is traceable, project history is readable, and the user always knows what changed in response to their request.
