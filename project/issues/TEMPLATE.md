# Issue Template

Copy this template when creating a new issue. The `/new-issue` skill fills it in automatically.

```markdown
# ISSUE-{hash}: {Title}

**Status:** Open | WIP | Closed
**Created:** YYYY-MM-DD
**Assignee:** Name | Unassigned
**Priority:** High | Medium | Low
**Labels:** bug, feature, docs, ...

## Capture
**Original request:** [verbatim quote from the user]

**Follow-up Q&A:** [questions asked and answers received, if any]

**Resolved intent:** [one paragraph in your own words — what this actually is and why]

## Description
[What, why, and any relevant context — can reference the capture above]

## Implementation Approach

### Files to modify
- `path/to/file.py` — one-line note on what changes

### Existing code to reuse
- `function_or_symbol` — `src/module.py:42` — why it's a fit

### Verification steps
- `make check` passes
- Manual: …

## Tasks
- [ ] Task description
- [✓] Completed task

## Subtasks
- [ ] ISSUE-{hash}-a: Research phase
- [⚒] ISSUE-{hash}-b: Implementation
- [✓] ISSUE-{hash}-c: Tests

## Relationships
- Depends on: [[ISSUE-{other-hash}-some-title]]
- Blocks: [[ISSUE-{other-hash}-some-title]]

## Comments
### YYYY-MM-DD - Author
Comment text...

## Implementation Log
<!-- issue-task:log-append -->
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
```

## Implementation Approach

This section is the workplan the reviewer reads. Fill it in before (or during) the first `🔧 impl:` commit — never later. Keep it concrete:

- **Files to modify:** actual paths, one per line. New paths are fine if they sit in an existing directory.
- **Existing code to reuse:** `symbol — path:line — why`. Forces you to read the code before writing tasks and stops the agent proposing code that already exists.
- **Verification steps:** at least one executable check — a command, a test name, or an explicit manual procedure. "Tests pass" alone is not a verification step.

Delete any subsection that truly doesn't apply (e.g. a pure-docs issue has no code to reuse) rather than leaving it empty.

## Subtasks

Append a letter suffix — `ISSUE-{hash}-a`, `ISSUE-{hash}-b`, `ISSUE-{hash}-c`. Subtasks are inline checklist items within the parent file only, not separate files. Do not use `[[double brackets]]` when referencing subtasks — subtasks have no files of their own.

Subtask states: `[ ]` not started → `[⚒]` in progress → `[✓]` complete

## Implementation Log entry format

```markdown
### YYYY-MM-DD HH:MM - LLM Implementation
**Action**: Implemented OAuth2 login flow
**Files Modified**:
- `src/auth/oauth.py` - Created OAuth handler
- `src/routes/auth.py` - Added login endpoints
**Commands Run**: `make test`
**Result**: Success — all tests passing
**Next**: Implement refresh token logic
```

Use the Comments section for decisions, blockers, and discussion. Use the Implementation Log for technical work.
