# ISSUE-079: Claude Code Setup Redesign — branching, hash IDs, CLAUDE.md diet

**Status:** Open
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** High
**Labels:** tooling, dx

## Capture

**Original request:** "I want you to do a critical audit of the claude-code setup (claude.md-files, skills, ...), some things to improve: claude-agents working in parallel conflict with each other. Each issue should be tackled on a separate branch. Redo the issue setup to work with branching and create a commit. The issue should be named after the opening commit 7-hash. (how would this work?!) Should we use more rules instead of having claude.md files scattered everywhere? Is there something we can learn from ~/repos/agent-skills??"

**Follow-up Q&A:**
- Q: Commit-hash-derived IDs have bootstrap paradox, break ~50 existing code references, and short hashes collide. Branches alone solve the parallel-conflict problem. Which route?
  A: Pushed back — "what if make use of different merge techniques some commit hash can be the issue-hash?" … then simplified: "owkay this is too complicated, can we just create a unique hash ourselves? the md.file should also have a date ISSUE-YYMMDD-{hash}-{slug} so it's a bit ordered."
- Q: How should the `wip/` directory behave under branching?
  A: wip/ lives only on branches (recommended option).
- Q: How aggressive should CLAUDE.md consolidation be?
  A: Adhere to Claude Code best practices (short, prune ruthlessly, long files make rules get lost); explore `.claude/rules/*.md` as a first-class mechanism.
- Q: Which agent-skills patterns to adopt?
  A: All four — anti-rationalization tables, progressive disclosure, explicit "when to use" frontmatter, specialist personas (deferrable).

**Resolved intent:** Redesign the Claude Code developer setup so (a) parallel agents can work on independent issues without conflicts via branch-per-issue, (b) issue IDs are self-generated random hex hashes with a date prefix for chronological ordering, eliminating the shared-counter collision problem, (c) always-loaded CLAUDE.md files are pruned from ~480 to ~150 lines with detailed process content extracted to on-demand reference files and skills, and (d) the highest-leverage patterns from `~/repos/agent-skills` (anti-rationalization tables, enriched skill frontmatter) are adopted without a full rewrite. Legacy ISSUE-001..078 remain untouched to avoid breaking ~50 source-code references.

## Description

The developer setup has grown organically to 479 lines of always-loaded CLAUDE.md content, a linear-history issue workflow that doesn't survive parallel agents, and skill docs that duplicate workflow content already in CLAUDE.md. Three concrete pains motivate the redesign — see the plan file at `~/.claude/plans/cozy-foraging-porcupine.md` for full audit results and approved plan.

This is the LAST issue under the legacy `ISSUE-NNN` scheme. Future issues use the new `ISSUE-YYMMDD-{hash}-{slug}.md` format.

## Tasks

- [ ] Create `project/FEATURE_WORKFLOW.md` with the extracted 8-step procedure
- [ ] Create `project/issues/TEMPLATE.md` with the issue template
- [ ] Create `project/issues/GIT_CONVENTIONS.md` with detailed commit/staging rules
- [ ] Shrink `project/CLAUDE.md` to ~60 lines (core rules only)
- [ ] Shrink `project/issues/CLAUDE.md` to ~50 lines, add anti-rationalization table
- [ ] Rewrite `.claude/skills/new-issue/SKILL.md` for branch + hash-ID flow; enrich frontmatter
- [ ] Rewrite `.claude/skills/finish-issue/SKILL.md` for branch-merge flow; add anti-rationalization table; enrich frontmatter
- [ ] Update `.claude/settings.json` SessionStart hook to show active `issue/*` branches
- [ ] Light trim of root `CLAUDE.md` and `docs/CLAUDE.md`
- [ ] End-to-end verification (parallel creation, full lifecycle, legacy lookup)

## Relationships

- Supersedes: the existing `ISSUE-NNN` counter-based scheme (documented in the current `project/issues/CLAUDE.md`)

## Comments

### 2026-04-15 - Plan approval
User approved the full plan at `~/.claude/plans/cozy-foraging-porcupine.md` after two rounds of simplification. Key decisions: self-generated 6-char hex hashes, date-prefixed filenames, branch-per-issue, wip/ only on branches, light-touch CLAUDE.md consolidation (no rules rewrite), defer specialist subagents beyond issue-reviewer.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
