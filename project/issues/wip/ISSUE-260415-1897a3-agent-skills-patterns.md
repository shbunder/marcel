# ISSUE-1897a3: Adopt three rationalization/discipline patterns from agent-skills

**Status:** Open
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** Low
**Labels:** docs, rules

## Capture
**Original request:** adopt three patterns from agent-skills repo: (1) add "Common rationalizations" subsections to .claude/rules/closing-commit-purity.md, .claude/rules/git-staging.md, and .claude/rules/docs-in-impl.md — each listing 2-3 common agent excuses paired with short rebuttals; (2) add a short "Prove-It" paragraph to project/CODING_STANDARDS.md instructing that bug fixes must begin with a failing repro test that becomes the regression guard; (3) create a new .claude/rules/debugging.md rule with the Reproduce → Localize → Reduce → Fix → Guard triage from agent-skills' debugging-and-error-recovery skill. Keep all additions concise and in Marcel's existing rule voice (Never/Always/Why). Do not import any agent-skills content wholesale — these are Marcel-voiced adaptations of the patterns only.

**Follow-up Q&A:** none — scope is explicit in the request. Prior assessment lives at `~/.claude/plans/sorted-bubbling-beacon.md`.

**Resolved intent:** Harden three of Marcel's existing rules and CODING_STANDARDS against the kinds of excuses agents reach for when cutting corners, and add a new debugging triage rule for when Claude starts guessing through broken tests. Each change is a small documentation edit in Marcel's own voice — no wholesale imports from the external agent-skills repo, no new infrastructure.

## Description
During a review of `~/repos/agent-skills` (see plan file `sorted-bubbling-beacon.md`), three patterns stood out as cheaply adoptable by Marcel:

1. **Common rationalizations tables.** agent-skills pairs common agent excuses with short rebuttals inside each SKILL. `project/issues/CLAUDE.md` already uses this pattern — extend it to the three most-load-bearing rules: closing-commit-purity, git-staging, and docs-in-impl.

2. **"Prove-It" for bug fixes.** A bug fix starts with a failing test that reproduces the bug; the test becomes the regression guard. Marcel has no explicit statement of this anywhere. Add a short paragraph to `CODING_STANDARDS.md`.

3. **Debugging triage.** A new short rule `.claude/rules/debugging.md` capturing the Reproduce → Localize → Reduce → Fix → Guard loop. Path: always-loaded (not path-scoped) so it applies whenever the agent is debugging.

All four files must read as native Marcel content — same tone as the existing rules, same section headings (`Never / Always / Why / Enforcement`), no agent-skills jargon (no "anti-rationalization," no "Stop-the-Line," no OWASP-style tables).

## Tasks
- [ ] Add "Common rationalizations" subsection to [.claude/rules/closing-commit-purity.md](../../../.claude/rules/closing-commit-purity.md) — 2–3 excuses with rebuttals
- [ ] Add "Common rationalizations" subsection to [.claude/rules/git-staging.md](../../../.claude/rules/git-staging.md) — 2–3 excuses with rebuttals
- [ ] Add "Common rationalizations" subsection to [.claude/rules/docs-in-impl.md](../../../.claude/rules/docs-in-impl.md) — 2–3 excuses with rebuttals
- [ ] Add a "Prove-It (bug fixes)" paragraph to [project/CODING_STANDARDS.md](../../CODING_STANDARDS.md)
- [ ] Create [.claude/rules/debugging.md](../../../.claude/rules/debugging.md) with Reproduce → Localize → Reduce → Fix → Guard triage
- [ ] Register the new debugging rule in [project/CLAUDE.md](../../CLAUDE.md) under "Enforceable rules"
- [ ] Grep for any other place that lists the rule set (e.g. `docs/claude-code-setup.md`) and update it
- [ ] Run `make check`

## Relationships
_(none)_

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
