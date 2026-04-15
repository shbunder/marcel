# Issue Management

Issues are tracked as markdown files in this directory, versioned with git. No external tools or databases — just files, text, and consistent conventions.

- **Template:** [TEMPLATE.md](./TEMPLATE.md) — copy this when writing an issue by hand. `/new-issue` fills it in automatically.
- **Git conventions:** [GIT_CONVENTIONS.md](./GIT_CONVENTIONS.md) — commit sequence, staging rules, merging, fixups, useful queries.

## Directory structure

```
./project/issues/
  open/    # Captured, branch not yet created (backlog) — lives on main
  wip/     # On a feature branch, work in progress — ONLY exists on feature branches
  closed/  # Completed or cancelled — reaches main via merge
```

`wip/` never appears on `main`. Active work is surfaced via `git branch --list 'issue/*'` (or `git worktree list` if using parallel agents).

## File naming (new scheme — ISSUE-079 onward)

`ISSUE-{YYMMDD}-{hash}-{brief-title}.md` — e.g. `ISSUE-260415-a1b2c3-conversation-summary-hallucination.md`

- `{YYMMDD}` is the UTC creation date, for chronological `ls` ordering
- `{hash}` is a 6-char random hex string generated at creation time (`python3 -c 'import secrets; print(secrets.token_hex(3))'`), collision-checked against existing files
- `{brief-title}` is kebab-case, 3–5 words, no stop words
- The short form `ISSUE-{hash}` is used in commit messages, code comments, and `[[...]]` wiki-links

The self-generated hash prevents *counter collisions* when two agents create issues at the same time. For true parallel work (two Claude Code sessions editing the repo simultaneously), see "Parallel agents — git worktrees" in [GIT_CONVENTIONS.md](./GIT_CONVENTIONS.md): hash IDs alone aren't enough because two sessions in the same checkout share one `HEAD`. Use `/parallel-issue` to spin up an isolated worktree.

Legacy issues (ISSUE-001 through ISSUE-078) use the old sequential counter and are NOT migrated. Treat them as read-only history.

## Lifecycle

1. **Create** via `/new-issue` — issue file written to `open/` on `main`, standalone `📝 [ISSUE-{hash}] created: ...` commit, then feature branch `issue/{hash}-{slug}` is created and checked out.
2. **Start work** on the feature branch — first `🔧 impl:` commit moves the file from `open/` to `wip/` and includes initial code changes.
3. **Progress** — update task/subtask checkboxes; append to Implementation Log for any code changes. Multiple `🔧 impl:` commits are fine.
4. **Close** via `/finish-issue` — moves file to `closed/` on the branch with `Status: Closed`, then `git merge --no-ff` back to main.

Before closing, verify:
- All tasks and subtasks show `[✓]`
- Dependent issues are unblocked and notified
- Implementation Log reflects all work done
- All files that reference changed conventions are updated — check skills (`~/.marcel/skills/`), defaults (`src/marcel_core/defaults/`), other CLAUDE.md files, and docs. A `grep` for key terms from your changes is the fastest way to catch stragglers.

**Closing is mandatory, not optional.** Every issue that reaches "code complete" must be formally closed in the same conversation. Use `/finish-issue` — it handles task status updates, implementation logging, verification, closure, and merging.

**Guardrail:** Before ending any conversation where you committed `🔧 [ISSUE-{hash}] impl:` commits, check whether the feature branch has been merged. If not, close and merge now — or explicitly tell the user the branch remains open and why.

## Commit format (quick reference)

```
📝 [ISSUE-{hash}] created: <description>   ← on main, standalone, issue file only
🔧 [ISSUE-{hash}] impl: <description>      ← on branch, issue file + source code
✅ [ISSUE-{hash}] closed: <summary>        ← on branch, standalone, status marker
🩹 [ISSUE-{hash}] fixup: <correction>      ← on main after merge, trivial corrections
```

Full staging rules, multi-commit patterns, and merge commands are in [GIT_CONVENTIONS.md](./GIT_CONVENTIONS.md).

## Common rationalizations (things you might try to skip)

| Excuse | Reality |
|--------|---------|
| "This is too trivial to need an issue" | Anything beyond a one-line typo needs an issue. The `📝` commit IS the audit trail. |
| "Tests can come in a follow-up PR" | Tests ship in the same closing commit as the code. No exceptions. |
| "I'll update docs in a fixup later" | Docs ship in the last `🔧 impl:` before close, not in a `🩹 fixup`. Fixups are for typos, not missing docs. |
| "The lessons-learned entry is optional" | It's not. Step 9 of `/finish-issue` is mandatory. |
| "I can work directly on main for this one, it's quick" | No. Parallel-agent conflicts are the exact problem this workflow fixes. |
| "I'll leave this `wip/` file around, I'll get back to it" | No. Close the issue or explicitly tell the user why it stays open. Invisible WIP debt accumulates silently. |
| "I can combine the code change and the close commit" | No. `✅ close` is a pure status marker. Code changes go in `🔧 impl:` before it. |
