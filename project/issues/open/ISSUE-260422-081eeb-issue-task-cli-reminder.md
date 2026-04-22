# ISSUE-081eeb: `issue-task` CLI helper + WIP-file session reminder hook

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** dev-experience, tooling, tokens

## Capture

**Original request:**

> I noticed that when claude-code makes changes to an issue (updating a task) it typically rewrites the entire task, meaning a lot of tokens are wasted on writing that file.
>
> Can we learn from ~/repos/clawcode how to better interact with the issue file in the same way clawcode interacts with its plan? Can we make better skills / subagents / scripts to enhance the issue-handling capability? (this is general claude-code logic, nothing to do with marcel specifically)
>
> I also notice claude code also does steps like "Update Todos", can we add similar capabilities for issues (Update Tasks)?

**Resolved intent:** Two coupled deliverables. (1) A deterministic `issue-task` CLI — modelled on Claude Code's `TodoWrite` structured tool — so checkbox flips, status changes, and Implementation Log appends become single Bash calls that cost tens of tokens instead of kilobyte-scale file rewrites. (2) A `UserPromptSubmit` reminder hook that mirrors Claude Code's plan-mode existence-branched instruction: when a WIP issue file exists on the current branch, inject a short system-reminder telling the agent to use `issue-task` (or `Edit`) — never `Write` a full rewrite. Discipline becomes mechanism.

## Description

### The problem we're solving

Today, `/finish-issue` step 4 says "Go through every `[ ]` and `[⚒]` and mark `[✓]`." The agent treats this as "restructure the file" and reaches for `Write`. A `Write` of a 150-line WIP issue file costs 3–8k output tokens to flip three checkboxes. The same three flips cost ~40 tokens as three small `Edit` calls, and ~30 tokens as a single `issue-task check "..."` Bash call.

Claude Code already solved this for plans:

- **Existence-branched prompt** — every turn in plan mode, a meta-user-message tells the agent "file exists → Edit; file missing → Write". See [utils/messages.ts:3328-3329](/home/shbunder/repos/clawcode/utils/messages.ts#L3328-L3329).
- **Capture-as-you-go** — "After each discovery, immediately capture what you learned. Don't wait until the end." ([utils/messages.ts:3345](/home/shbunder/repos/clawcode/utils/messages.ts#L3345)).
- **TodoWrite** — structured tool, not file edits. Agent describes intent; the harness manages state.

This issue ports all three ideas to Marcel's issue-file workflow.

### The CLI

`.claude/scripts/issue-task` — a single Python entry point with subcommands. Auto-discovers the WIP issue file on the current branch (expects exactly one `project/issues/wip/*.md`; errors if zero or multiple). All mutations are in-place and idempotent where sensible.

```
issue-task check <task-regex>              # flip [ ] or [⚒] → [✓] on the first matching task line
issue-task start <task-regex>              # flip [ ] → [⚒]
issue-task reopen <task-regex>             # flip [✓] or [⚒] → [ ]
issue-task add "<task description>"        # append "- [ ] <desc>" under ## Tasks
issue-task status <Open|WIP|Closed>        # replace the Status: header line
issue-task log "<action>" [--files ...]    # append an Implementation Log entry using the TEMPLATE format
issue-task show                            # print the resolved WIP file path (for scripts/skills)
```

Match semantics: `<task-regex>` is a case-insensitive Python regex matched against the task line text (after the `- [x]` prefix). If it matches multiple lines, the command fails loud with "ambiguous match, N hits" and lists them — no guessing.

Exit codes: `0` success, `1` usage error, `2` no WIP file found, `3` ambiguous match, `4` no match.

### The reminder hook

`.claude/hooks/issue-reminder.py` — a `UserPromptSubmit` hook registered in `.claude/settings.json`. Every turn, it:

1. Runs `git branch --show-current` and globs `project/issues/wip/*.md`.
2. If a WIP file exists, emits a short system-reminder (stdout per the hook protocol) like:

   > *"A WIP issue file exists at `{path}`. For task checkboxes, status changes, and Implementation Log entries, use `.claude/scripts/issue-task` (see `issue-task --help`). Use `Edit` for free-form prose. Do NOT use `Write` on the issue file — a full rewrite costs orders of magnitude more tokens than a targeted mutation."*

3. If no WIP file, emits nothing (hook stays silent).

The hook must be fast (sub-50ms — it runs on every prompt). That's trivial: one `git` call, one `glob`, one conditional print.

### Why this is two files, not one

The CLI alone fails if the agent doesn't remember to use it. The hook alone fails because "use Edit not Write" is a discipline, not a mechanism. Together: the hook points at the CLI, the CLI replaces the need for Edit in the common case, and Edit stays as the fallback for prose. Each reinforces the other.

### Out of scope (deliberately)

- **PreToolUse blocking hook** that refuses `Write` on WIP issue files. More invasive, risks false positives (e.g., rewriting a malformed file). Revisit if the reminder hook proves insufficient in practice.
- **MCP server exposing `IssueTaskUpdate` as a first-class tool.** More TodoWrite-like, but 10× the setup and a new moving part. The Bash-CLI approach matches Marcel's "lightweight over bloated" principle.
- **Migrating legacy issues.** Existing `open/` and `closed/` files are not touched.

## Tasks

- [ ] Add `.claude/scripts/issue-task` (executable Python 3) with the seven subcommands above. Self-contained — no dependencies beyond stdlib.
- [ ] Implement WIP-file auto-discovery: glob `project/issues/wip/*.md`, error with exit 2 if zero matches, error with exit 3 (and list candidates) if multiple.
- [ ] Implement task-line mutation with ambiguity detection — case-insensitive regex match, fail loud on multi-match.
- [ ] Implement Implementation Log appending: use the exact format from [TEMPLATE.md](project/issues/TEMPLATE.md) (`### YYYY-MM-DD HH:MM - LLM Implementation` etc.). Insert under the `## Implementation Log` header comment; append if entries already exist.
- [ ] Add `tests/claude/test_issue_task.py` covering: each subcommand happy-path, no-WIP-file error, ambiguous-match error, status-flip idempotency, log entry append to empty vs non-empty section.
- [ ] Add `.claude/hooks/issue-reminder.py` UserPromptSubmit hook that emits the system-reminder when a WIP file exists on the current branch.
- [ ] Register the hook in `.claude/settings.json` under `hooks.UserPromptSubmit`.
- [ ] Update [.claude/skills/finish-issue/SKILL.md](.claude/skills/finish-issue/SKILL.md) step 4 to invoke `issue-task check` / `start` / `reopen` in a Bash loop instead of manual Edit. Step 5 uses `issue-task log`. Step 8's `Status: Closed` flip uses `issue-task status Closed`.
- [ ] Update [.claude/skills/new-issue/SKILL.md](.claude/skills/new-issue/SKILL.md) to reference `issue-task status WIP` + `issue-task log` in the first `🔧 impl:` commit flow (when the file moves from `open/` to `wip/`, status and log should be set via the helper).
- [ ] Update [project/issues/CLAUDE.md](project/issues/CLAUDE.md) with a short "Updating issue files" section pointing at `issue-task --help`.
- [ ] Add a stable anchor comment to [project/issues/TEMPLATE.md](project/issues/TEMPLATE.md) Implementation Log and Lessons Learned sections (e.g., `<!-- issue-task:log-append -->`) so the CLI's insertion point is deterministic.
- [ ] `make check` passes (format, lint, typecheck, tests, 90% coverage).
- [ ] Smoke-test end-to-end on this issue itself: the closing `/finish-issue` flow uses the new helper for every checkbox and log entry, and the reminder hook fires from turn 1.

## Relationships

- Depends on: [[ISSUE-83ee76-git-tool-test-hook-leak]] — the fix that unblocks clean commits in this workflow

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
