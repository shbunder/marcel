# Marcel Developer Guide

This file governs coder mode — when Marcel is being extended, debugged, or rewriting its own code. For a definition of when coder mode applies, see [CLAUDE.md](../CLAUDE.md#2-coder--self-rewriting-agent).

The **how** is as important as the **what**. A working feature that breaks the architecture or makes the next change harder is not a good outcome. Take the time to do it right.

## Philosophy

Core principles are defined in [CLAUDE.md](../CLAUDE.md#core-principles). All development work must follow them.

## Standards

Detailed coding guidelines — style, API design, type system, and error handling — are in [CODING_STANDARDS.md](./CODING_STANDARDS.md).

## Project Management

Issues and feature requests are tracked as markdown files under `./project/issues/`. See [./issues/CLAUDE.md](./issues/CLAUDE.md) for the full conventions: file naming, lifecycle (open → wip → closed), git commit format, linking, and implementation logging.

When a user requests a new feature or reports a bug, create an issue in `./project/issues/open/` before starting implementation. This keeps work discoverable and the project history readable.

## Feature Development Procedure

Every feature or bug fix follows this procedure. Some steps are skippable for small changes — see the note at each step.

**A small change** is one that: touches at most one existing file, introduces no new public interface, and can be described in one sentence. If in doubt, treat it as substantial.

## Step 1 — Capture

Before starting, read `project/lessons-learned.md` to check for relevant patterns or pitfalls from past issues.

Record the original request verbatim, all follow-up questions asked, and the user's answers. End with a one-paragraph **resolved intent**: what the feature actually is, in your own words.

Record these in the issue file you'll create in Step 3. Hold them in working memory until then — the important thing is nothing is lost before it's written down.

> Always do this. Even for small requests, the resolved intent prevents silent misunderstandings.

## Step 2 — Requirements

Derive clear, testable requirements from the capture. Each requirement should state an observable behavior, not an implementation detail. This is what "done" looks like.

Before continuing:
- Read existing related code to understand current patterns
- Check whether a similar skill or integration already exists
- Identify where the change belongs (skill, integration, config, core)

If the request is vague or conflicts with existing architecture, ask rather than guess. A wrong implementation is worse than a delayed one.

> Always do this.

## Step 3 — Create an issue

Create an issue in `./project/issues/open/` per the conventions in [./issues/CLAUDE.md](./issues/CLAUDE.md). **Commit the issue file immediately** — this is a standalone `📝` commit. The issue moves to `wip/` in the first implementation commit (step 7).

> Always do this for anything beyond a small change.

## Step 4 — Design *(skip for small changes)*

For substantial features, sketch the approach before writing code: which files change, what the public interface looks like (integration handler signature, skill contract, config shape). Confirm with the user before proceeding.

> Skip when the change is confined to one file and the interface is obvious.

## Step 5 — Scaffold *(skip for small changes)*

Create the file structure and function/class signatures with no logic — just enough shape for tests to compile against.

> Skip when there is no new file structure or interface to define.

## Step 6 — Tests

Write tests derived from the requirements, not from the implementation. Tests go in `tests/` and should cover all reachable code paths.

Use `make test` to run them. They should fail at this point (red) — that's expected.

> For small changes: write tests alongside the implementation instead of before.

## Step 7 — Implement

Fill in the logic. Keep changes minimal and focused — do not refactor unrelated code while implementing a feature.

Run `make test` regularly. The goal is to go green.

## Step 8 — Ship

Run `make check` — this runs format, lint, typecheck, and tests with coverage. All must pass before the work is considered done.

```
make check
```

Log implementation work in the issue file (Implementation Log section).

**Pre-close verification.** Before creating the closing commit, run through the verification checklist in [./issues/CLAUDE.md](./issues/CLAUDE.md) — especially check that all files referencing changed conventions (skills, docs, other CLAUDE.md files) have been updated. This prevents post-close fixups.

**Closing commit.** Create a **separate closing commit** that contains:
- The issue file moved from `wip/` to `closed/` with `Status: Closed`
- Documentation updates in `docs/` per [docs/CLAUDE.md](../docs/CLAUDE.md)
- Version bump per [VERSIONING.md](./VERSIONING.md)
- **No code changes** — all code must already be committed in implementation commits

If you discover a missed item after closing, use a `🩹 fixup` commit — see [./issues/CLAUDE.md](./issues/CLAUDE.md) for rules.

**Push to the user branch.** After committing, push the changes to a remote branch named after the requesting user. This lets the user review and merge to `main` at their own pace:

```bash
git push origin HEAD:shaun
```

Replace `shaun` with the slug of the user who requested the feature. The branch is created if it doesn't exist. Do **not** force-push — append only, so the review history is preserved.

**Trigger a restart.** After pushing, signal the restart mechanism to redeploy Marcel with the new code:

```python
from marcel_core.watchdog.flags import request_restart
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD~1']).decode().strip()
request_restart(sha)  # writes flag file → host systemd triggers redeploy
```

This writes the `restart_requested` flag file. A host-side systemd path unit (`marcel-redeploy.path`) watches for this file and triggers `redeploy.sh` on the host — which rebuilds the Docker image, restarts the container, health-checks, and rolls back on failure. Marcel does **not** restart itself from inside the container.

In dev mode (`make serve`), the restart watcher in `main.py` detects the flag and exec-replaces the process in-place. See [docs/self-modification.md](../docs/self-modification.md) for full details.

## Self-Modification Safety

When rewriting Marcel's own code:

- Commit before restarting — every change must be recoverable via git revert
- Always trigger restart through `request_restart()` — never `systemctl restart` or `docker restart` directly. The flag-based mechanism provides the rollback safety net.
- Confirm with the user before restarting unless they explicitly asked for an auto-restart
- Keep changes minimal and focused — don't refactor unrelated code while implementing a feature
- **Restricted files:** Auth logic, core config, and safety rules (including CLAUDE.md files) are off-limits. If a change touches one of these areas, confirm with the user before proceeding even if they did not explicitly request confirmation.

## Integration Pattern

New integrations follow this pattern:

1. **Create a python integration module** at `src/marcel_core/skills/integrations/<name>.py`. Use the `@register("name.action")` decorator to register async handler functions. Each handler receives `(params: dict, user_slug: str)` and returns a string.
2. **Create a skill doc** at `src/marcel_core/skills/docs/<name>/SKILL.md`. This teaches the agent how to call `integration(skill="name.action", params={...})` with inline examples, parameter tables, and usage notes. Run `make install-skills` to symlink it into `.claude/skills/` (happens automatically with `make serve`).
3. **For simple HTTP/shell integrations**, add a JSON entry to `skills.json` instead — no Python module needed.
4. **Add the new skill directory to `.gitignore`** — e.g. `.claude/skills/<name>/` — so the generated symlink is not tracked.

All integrations are dispatched through the `integration` tool. The agent also has access to `memory_search` (keyword search across memory files) and `notify` (progress updates). These three tools are registered as MCP tools in `skills/tool.py` and passed to the `ClaudeSDKClient` session via `build_skills_mcp_server()`.

Integrations must be self-contained — they should not require changes to core Marcel code (tool.py, executor.py, runner.py). When adding an integration, verify the pattern works end-to-end before committing.

## Telegram-Initiated Changes

When a user requests a code change or feature **via Telegram**, the following rules apply without exception:

1. **Create an issue first** — open an issue in `./project/issues/open/` and commit it (`📝`) before writing any code. Follow the full commit workflow in [./issues/CLAUDE.md](./issues/CLAUDE.md).
2. **Follow the full Feature Development Procedure** — capture, requirements, issue, implement, ship. No shortcuts because the request came through chat.
3. **Respond via Telegram when done** — after committing, send the user a Telegram message containing:
   - The exact `git log --oneline -1` output (commit hash + message)
   - A brief summary of the Implementation Log from the issue file (what changed and why)

   Use the `notify` tool or the Telegram bot directly to deliver this. The user should not need to check git to know what happened.

This rule exists so that all work is traceable, the project history is readable, and the user always knows what changed in response to their request.

## User Data Rule

**User-specific information always goes in `~/.marcel/users/{slug}/`, never in `.env` or `.env.local`.**

This applies to:
- Integration credentials tied to a specific user (Apple ID, OAuth tokens, app-specific passwords)
- Per-user preferences, facts, and context
- Any data that would differ across users

The `.env` / `.env.local` files are for **system-wide** config only (API keys for shared services, port numbers, feature flags). Mixing user data into the environment makes multi-user support impossible and leaks one user's data into another's context.

When a user provides personal credentials or preferences:
1. Store them in `~/.marcel/users/{slug}/memory/{topic}.md` (or `profile.md` for core identity info)
2. Update `~/.marcel/users/{slug}/memory/index.md` with a one-liner
3. If the runtime needs the value at startup (e.g. an iCloud password), write it to `.env.local` **and** record the fact that it lives there in the memory file — never store the secret value itself in memory

See [docs/storage.md](../docs/storage.md) for the full storage API and file format.
