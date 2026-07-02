# Feature Development Workflow

Work is tracked on the **marcel-admin board** (a separate repo, an OKF bundle) as
**Feature → Story → Subtask**; code is implemented in *this* repo on a per-feature branch. Every
feature or bug fix follows this procedure. Some steps are skippable for small changes.

**A small change** is one that: touches at most one existing file, introduces no new public
interface, and can be described in one sentence. If in doubt, treat it as substantial.

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"   # the board + knowledge base
JIRA="$ADMIN/JIRA/jira.py"
```

## Step 1 — Capture

Before starting, check for relevant patterns or pitfalls from past work:

```bash
python scripts/query_lessons.py <keyword> [keyword ...]   # historical lessons (archived closed issues)
python3 "$JIRA" list --status Done                        # recent features on the board
```

Record the original request verbatim, all follow-up questions, and the user's answers. End with a
one-paragraph **resolved intent** in your own words. This goes into the **requirements page** you
create in Step 3 — hold it in working memory until then.

> Always do this. Even for small requests, the resolved intent prevents silent misunderstandings.

## Step 2 — Requirements

Derive clear, testable requirements from the capture — each an observable behavior, not an
implementation detail. Before continuing: read existing related code, check for an existing skill /
integration, and identify where the change belongs (skill, integration, config, core). If the
request is vague or conflicts with the architecture, ask rather than guess.

When a requirement is under-specified and you cannot settle it yet, **mark the gap** with an inline
`[NEEDS CLARIFICATION: …]` in the requirements page rather than recording a guess as fact. The
feature cannot leave `Backlog` while any marker is live — `/new-feature` surfaces them (step 5) and
both `jira set` and `plan-verifier` refuse to let it advance — so resolve each (settle it, delete the
marker, log the answer under the page's `## Clarifications` section) before the feature moves on.

> Always do this.

## Step 3 — Create a feature

Use `/new-feature` — it allocates `FEAT-{YYMMDD}-{hash}` on the board, scaffolds the **requirements
page** (fill it with the capture + Gherkin acceptance criteria), and prints the `feat/FEAT-…-slug`
branch name to create in this repo. Record any genuine architecture decision as an **ADR** with
`/new-adr`. Commit the ticket + docs in `marcel-admin`; create the branch here. See
[issues/CLAUDE.md](./issues/CLAUDE.md) for where the old system went and
`$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md` for the full conventions.

Then break the feature into **stories** with `/new-story FEAT-… "…"` — each a unit of work with its
own acceptance criteria and subtasks.

> Always do this for anything beyond a small change.

## Step 4 — Design *(skip for small changes)*

For substantial features, sketch the approach before writing code: which files change, the public
interface (handler signature, skill contract, config shape). Capture the load-bearing choice as an
ADR. Confirm with the user before proceeding.

> Skip when the change is confined to one file and the interface is obvious.

## Step 5 — Scaffold *(skip for small changes)*

Create the file structure and function/class signatures with no logic — just enough shape for tests
to compile against.

> Skip when there is no new file structure or interface to define.

## Step 6 — Tests

Write tests derived from the story acceptance criteria, not from the implementation. Tests go in
`tests/` and cover all reachable code paths. `make test` — they should be red at this point.

> For small changes: write tests alongside the implementation instead of before.

## Step 7 — Implement

Fill in the logic on the `feat/FEAT-…-slug` branch. **Code commits are prefixed `[STORY-…]`.** Keep
changes minimal and focused — don't refactor unrelated code. Track progress on the board as you go
(committed in `marcel-admin`, never folded into code commits):

```bash
python3 "$JIRA" check STORY-… <n>                     # tick a subtask
python3 "$JIRA" check STORY-… <n> --section acceptance
python3 "$JIRA" note  STORY-… "<what landed>"
python3 "$JIRA" set   STORY-… status "In Progress"    # …then Done when its acceptance criteria are tested
```

Run `make test` regularly. The goal is green.

## Step 8 — Ship

Run `make check` (format, lint, typecheck, tests with coverage) — all must pass before merge. A
pre-commit hook enforces it.

**Bypass for emergencies only:** `git commit --no-verify` — if the hook itself is broken or you're
committing a non-code (docs/board) change while the hook fails for unrelated reasons. Use sparingly.

**Close with `/finish-feature`.** It verifies every story is `Done`, runs `make check`, delegates to
the `pre-close-verifier` subagent, **merges the feature branch back to `main` with `--no-ff`**, and
flips the feature to `Done` on the board. There is no separate code-side "close commit" — the close
marker is the board status commit in `marcel-admin`. **Never leave a feature's branch unmerged at
the end of a conversation** without telling the user why.

**Trigger a restart.** After merging, signal the restart mechanism to redeploy Marcel:

```python
from marcel_core.watchdog.flags import request_restart
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD~1']).decode().strip()
request_restart(sha)  # writes flag file → host systemd triggers redeploy
```

This writes the `restart_requested.{env}` flag file (suffix from `MARCEL_ENV` — `prod` or `dev`).
The matching host-side systemd path unit (`marcel-redeploy.path` / `marcel-dev-redeploy.path`)
watches the flag and triggers `redeploy.sh --env {env}`, which clears the flag, rebuilds the image,
and recreates the container. Prod's in-container watchdog (PID 1) then polls `/health` and rolls
back via `git revert HEAD` on failure; dev has no watchdog by design. Marcel does **not** restart
itself from inside the container. See [docs/self-modification.md](../docs/self-modification.md).
