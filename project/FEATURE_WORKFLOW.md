# Feature Development Workflow

Every feature or bug fix follows this procedure. Some steps are skippable for small changes.

**A small change** is one that: touches at most one existing file, introduces no new public interface, and can be described in one sentence. If in doubt, treat it as substantial.

## Step 1 — Capture

Before starting, check for relevant patterns or pitfalls from past issues. Grep both files rather than reading them — the active file is capped at 10 entries, the archive holds everything older:

```bash
# Use 1-3 keywords from the resolved intent / feature area
grep -n -i -B 1 -A 20 '<keyword>' project/lessons-learned.md project/lessons-learned-archive.md
```

Reading the full file is wasteful — the archive can grow indefinitely and most entries won't be relevant to the current task.

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

Use `/new-issue` to create an issue in `./project/issues/open/` with a self-generated hash ID and immediately branch `issue/{hash}-{slug}`. The creation `📝` commit lives on `main`; all subsequent work happens on the feature branch. See [project/issues/GIT_CONVENTIONS.md](./issues/GIT_CONVENTIONS.md) for commit-message and staging rules, and [project/issues/TEMPLATE.md](./issues/TEMPLATE.md) for the file template.

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

Fill in the logic on the feature branch. The first `🔧` commit also moves the issue file from `open/` to `wip/`. Keep changes minimal and focused — do not refactor unrelated code while implementing a feature.

Run `make test` regularly. The goal is to go green.

## Step 8 — Ship

Run `make check` — this runs format, lint, typecheck, and tests with coverage. All must pass before the work is considered done.

```
make check
```

A git pre-commit hook automatically enforces this requirement — any attempt to commit code that doesn't pass `make check` will be blocked. The hook runs with a 120-second timeout and displays helpful error messages on failure.

**Bypass for emergencies:** If you need to commit despite failing checks (e.g., the hook itself is broken, or you're committing a WIP state for backup), use `git commit --no-verify`. Use this sparingly — it bypasses quality gates and can introduce broken code into the git history.

Log implementation work in the issue file (Implementation Log section).

**Pre-close verification.** Before creating the closing commit, run through the verification checklist in [project/issues/CLAUDE.md](./issues/CLAUDE.md) — especially check that all files referencing changed conventions (skills, docs, other CLAUDE.md files) have been updated. This prevents post-close fixups.

**Closing commit.** Use `/finish-issue` to create a separate closing commit on the feature branch that contains:
- The issue file moved from `wip/` to `closed/` with `Status: Closed`
- No code changes — all code must already be committed in `🔧 impl:` commits
- No docs/version bumps — those ship in a final `🔧 impl:` commit *before* the close

Then merge back to main with `git merge --no-ff`. If you discover a missed item after closing, use a `🩹 fixup` commit on main — see [project/issues/GIT_CONVENTIONS.md](./issues/GIT_CONVENTIONS.md) for rules.

**Never leave an issue in `wip/` at the end of a conversation.** If you shipped code under an issue, close it before the session ends. An issue left in `wip/` on a feature branch is invisible debt that accumulates silently.

**Trigger a restart.** After merging, signal the restart mechanism to redeploy Marcel with the new code:

```python
from marcel_core.watchdog.flags import request_restart
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD~1']).decode().strip()
request_restart(sha)  # writes flag file → host systemd triggers redeploy
```

This writes the `restart_requested` flag file. A host-side systemd path unit (`marcel-redeploy.path`) watches for this file and triggers `redeploy.sh` on the host — which rebuilds the Docker image, restarts the container, health-checks, and rolls back on failure. Marcel does **not** restart itself from inside the container.

In dev mode (`make serve`), the restart watcher in `main.py` detects the flag and exec-replaces the process in-place. See [docs/self-modification.md](../docs/self-modification.md) for full details.
