---
name: new-feature
description: Create a new Feature (epic) on the marcel-admin board — its requirements page, optional ADR(s), and the feature branch in the marcel code repo. Use when the user describes a new capability worth tracking. For a unit of work under an existing feature use /new-story; for a one-line typo, skip tracking entirely.
---

Create a new feature for: $ARGUMENTS

The board and knowledge base live in the **separate `marcel-admin` repo** (an OKF bundle). Conventions are the source of truth in `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md` and `$MARCEL_ADMIN_DIR/WIKI/CLAUDE.md`. This skill is the procedural wrapper around `jira.py` — let the CLI do the mechanical parts. Stage by name in both repos (per [.claude/rules/git-staging.md](../../rules/git-staging.md)).

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"
JIRA="$ADMIN/JIRA/jira.py"
```

## Steps

### 1. Ensure a clean base in the code repo

```bash
git checkout main && git pull --ff-only && git status   # must be clean
```

If the tree is dirty or you're on another feature branch, stop and tell the user to finish or stash first. For a parallel second agent, use `/parallel-feature` instead (it makes a worktree).

### 2. Allocate the feature + its requirements page

```bash
python3 "$JIRA" new-feature "<Title>" --priority <High|Medium|Low>
```

This prints the `FEAT-{YYMMDD}-{hash}` id, the requirements path, and the `feat/FEAT-…-slug` branch name. The requirements page is scaffolded automatically (1:1 with the feature).

### 3. Write the prose (requirements first, then the feature)

**Before writing: never ask the user what reading the code can answer.** Explore the marcel codebase; reserve questions for requirements, tradeoffs, and preferences.

- **Requirements page** (`$ADMIN/WIKI/requirements/FEAT-…-slug.md`) — fill the fixed template: Context & problem, Goals & non-goals, **Scenarios (Gherkin acceptance criteria)**, Functional / Non-functional requirements, Out of scope, Open questions. Set the frontmatter `description:`.
- **Feature file** (`$ADMIN/JIRA/features/FEAT-…-slug.md`) — write the **Summary** paragraph, set the frontmatter `description:` (one sentence) and `tags:`, and add one `- [ ]` **Acceptance criteria** bullet per Gherkin scenario (the JIRA copy is the checklist; the requirements page is the detail).

### 4. Record architecture decisions as ADR(s)

For each genuine architecture decision the feature forces, create an ADR (independent log, 0..n per feature):

```bash
python3 "$JIRA" new-adr FEAT-… "<short decision title>"
```

Fill the MADR template (Context, Drivers, Considered options, Decision outcome, Consequences) in `$ADMIN/WIKI/decisions/ADR-…-slug.md`, then `python3 "$JIRA" set ADR-… status Accepted` once decided. The CLI appends the ADR id to the feature's `decisions:` list. Skip if the feature introduces no new architecture decision.

### 5. Resolve clarifications *(the clarify gate)*

The requirements you just wrote may carry `[NEEDS CLARIFICATION: …]` markers wherever something was under-specified. Settle them **now** — a feature cannot leave `Backlog` while any marker is live (`jira set` refuses the status flip and `plan-verifier` BLOCKs).

```bash
python3 "$JIRA" clarifications FEAT-…      # lists live markers; exit 1 while any remain
```

For each marker: ask the user the question, settle it, then **delete the marker from the requirements prose and log the question + answer under the page's `## Clarifications` section**. Re-run until it reports none. A genuinely deferred question is not a marker — move it to `## Out of scope` or leave it under `## Open questions` instead.

### 6. Commit the ticket + docs in marcel-admin (NOT the code repo)

```bash
git -C "$ADMIN" add JIRA/features/FEAT-….md WIKI/requirements/FEAT-….md WIKI/decisions/ADR-*.md log.md
git -C "$ADMIN" commit -m "FEAT-…: create <slug> — feature, requirements, ADR(s)"
```

`marcel-admin` stays on `main` (it is the board). Ticket/doc edits never go into code commits.

### 7. Create the feature branch in the code repo

```bash
git checkout -b "feat/FEAT-…-slug"
```

All of this feature's stories are implemented on this one branch. The first code commit is prefixed `[STORY-…]` (create stories with `/new-story` first).

### 8. Verify the plan is concrete *(skip for trivial features)*

Invoke the [`plan-verifier`](../../agents/plan-verifier.md) subagent via the `Agent` tool. Inputs: the requirements page path, the feature file path, and the ADR path(s). It checks that the requirements + decision are concrete enough to execute against and returns an advisory verdict (APPROVE / WARN / BLOCK). It **BLOCKs on any surviving `[NEEDS CLARIFICATION]` marker**, so make sure step 5 left none. Fix BLOCKs; address or justify WARNs. Skip for docs-only or one-file features.

### 9. Report back

Tell the user: the `FEAT-…` id, the branch name, the requirements + ADR paths, and the acceptance criteria you wrote — so they can confirm scope before stories are broken out with `/new-story FEAT-… "…"`.
