---
name: new-issue
description: Create a new issue file AND its feature branch with a self-generated hash ID. Use when the user describes new work — bugs, features, refactors — that warrants tracking. Do NOT use for one-line typo fixes.
---

Create a new issue for: $ARGUMENTS

Full conventions live in [project/issues/CLAUDE.md](../../../project/issues/CLAUDE.md) and [project/issues/GIT_CONVENTIONS.md](../../../project/issues/GIT_CONVENTIONS.md). The template is [project/issues/TEMPLATE.md](../../../project/issues/TEMPLATE.md). This skill is the procedural wrapper.

**If the user wants to run another Claude Code session in parallel on this issue**, use `/parallel-issue` instead — it creates a git worktree so the two sessions don't stomp each other's `HEAD`. This single-checkout flow is the right default for one-agent-at-a-time work.

## Steps

### 1. Ensure clean main

```bash
git checkout main
git pull --ff-only
git status  # must be clean
```

If the working tree is dirty or you're already on a feature branch, stop and tell the user — they need to finish or stash existing work first.

### 2. Generate a collision-free hash

```bash
while true; do
  HASH=$(python3 -c 'import secrets; print(secrets.token_hex(3))')
  if [ -z "$(ls project/issues/*/ISSUE-*-${HASH}-*.md 2>/dev/null)" ]; then
    break
  fi
done
```

6 hex chars, retry loop guards against the (practically impossible) collision case.

### 3. Derive date and slug

```bash
DATE=$(date +%y%m%d)
```

Slug: produce a short kebab-case slug from the request (3–5 words, no stop words). Filename will be `project/issues/open/ISSUE-${DATE}-${HASH}-${SLUG}.md`.

### 4. Plan mode — conditional escape hatch

**Enter plan mode if any of these apply:**
- The user literally said "plan this", "plan first", "/plan", or equivalent.
- The request is genuinely ambiguous about approach — multiple reasonable implementations that a human would want to choose between.
- The agent's best guess is that the work will touch more than ~3 files, or introduces a new architectural piece (new integration, new subsystem, cross-cutting refactor).

**Otherwise, skip this step** and proceed straight to Step 5. Most issues should.

When plan mode applies:

1. Call the `EnterPlanMode` tool. You are now read-only except for the plan file.
2. Explore the codebase, write the plan incrementally, batch clarifying questions via `AskUserQuestion` (never ask what reading the code can answer — Step 5's heuristic applies here too).
3. When the plan is complete and you're ready for user approval, call `ExitPlanMode`.
4. **On approval, transcode the plan file's contents into the issue template's sections:**
   - *What the feature is / why* → `## Description`
   - *Files to modify / Existing code to reuse / Verification steps* → `## Implementation Approach`
   - *Concrete checklist* → `## Tasks`

   The plan file in `~/.claude/plans/` is a throwaway session artifact — the issue file is the source of truth. Do not keep the plan file in sync with the issue afterwards.

5. Proceed to Step 5 with the fill-in list below.

### 5. Write the issue file

**Before filling the file: never ask the user what reading the code can answer.** Explore first; reserve questions for requirements, tradeoffs, or preferences — the things only the user can decide.

Use the template in [project/issues/TEMPLATE.md](../../../project/issues/TEMPLATE.md). Fill in:

- **ID in the heading:** `# ISSUE-${HASH}: {Title}`
- **Status:** Open
- **Created:** today's date (YYYY-MM-DD, full form, inside the file)
- **Capture → Original request:** verbatim from `$ARGUMENTS`
- **Resolved intent:** one paragraph in your own words
- **Description:** what and why, derived from the request
- **Tasks:** a concrete, testable checklist for this issue
- Leave Relationships empty unless you can infer dependencies from existing issues

### 6. Commit the issue file on main (standalone `📝`)

```bash
git add "project/issues/open/ISSUE-${DATE}-${HASH}-${SLUG}.md"
git commit -m "📝 [ISSUE-${HASH}] created: ${SLUG} — one-line description"
```

Stage only the issue file — per [.claude/rules/git-staging.md](../../rules/git-staging.md), never `git add .` or `git add -A`.

### 7. Create the feature branch

```bash
git checkout -b "issue/${HASH}-${SLUG}"
```

All subsequent work for this issue happens on this branch. The user is now ready to start implementation.

The first `🔧 impl:` commit will move the file from `open/` to `wip/` and flip the `Status:` line. Use the helper rather than rewriting the file:

```bash
git mv "project/issues/open/ISSUE-${DATE}-${HASH}-${SLUG}.md" "project/issues/wip/ISSUE-${DATE}-${HASH}-${SLUG}.md"
.claude/scripts/issue-task status WIP
.claude/scripts/issue-task log "<one-line description>" --files <changed file paths>
```

Subsequent task / status / log changes also use `issue-task` — see `.claude/scripts/issue-task --help` and [project/issues/CLAUDE.md](../../../project/issues/CLAUDE.md). The `UserPromptSubmit` reminder hook will surface this every turn while a WIP file exists.

**Fill in the Implementation Approach section of the issue file as part of (or before) the first `🔧 impl:` commit** — real file paths, specific reusable symbols with `path:line`, executable verification steps. See [project/issues/TEMPLATE.md](../../../project/issues/TEMPLATE.md) for the schema.

**After the first `🔧 impl:` commit, invoke the [`plan-verifier`](../../agents/plan-verifier.md) subagent** via the `Agent` tool. Inputs: the wip issue file path and the branch name. It returns a structured advisory verdict (APPROVE / WARN / BLOCK). Fix BLOCKs before continuing; address or justify WARNs in the Implementation Log. **Skip for trivial issues** (`docs`-only labels, typo/one-file fixes, or the user explicitly said "trivial") — invoke with the `trivial` flag instead, which short-circuits to APPROVE.

### 8. Report back

Tell the user:
- The hash ID (`ISSUE-${HASH}`)
- The branch name (`issue/${HASH}-${SLUG}`)
- The file path
- The task list you created, so they can confirm or adjust scope before work begins
