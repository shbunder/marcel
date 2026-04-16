# ISSUE-1897a3: Adopt three rationalization/discipline patterns from agent-skills

**Status:** Closed
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
- [✓] Add "Common rationalizations" subsection to [.claude/rules/closing-commit-purity.md](../../../.claude/rules/closing-commit-purity.md) — 2–3 excuses with rebuttals
- [✓] Add "Common rationalizations" subsection to [.claude/rules/git-staging.md](../../../.claude/rules/git-staging.md) — 2–3 excuses with rebuttals
- [✓] Add "Common rationalizations" subsection to [.claude/rules/docs-in-impl.md](../../../.claude/rules/docs-in-impl.md) — 2–3 excuses with rebuttals
- [✓] Add a "Prove-It (bug fixes)" paragraph to [project/CODING_STANDARDS.md](../../CODING_STANDARDS.md)
- [✓] Create [.claude/rules/debugging.md](../../../.claude/rules/debugging.md) with Reproduce → Localize → Reduce → Fix → Guard triage
- [✓] Register the new debugging rule in [project/CLAUDE.md](../../CLAUDE.md) under "Enforceable rules"
- [✓] Grep for any other place that lists the rule set (e.g. `docs/claude-code-setup.md`) and update it
- [✓] Run `make check`

## Relationships
_(none)_

## Implementation Log

### 2026-04-15 - LLM Implementation
**Action**: Adopted three discipline patterns from `~/repos/agent-skills` in Marcel's own voice.
**Files Modified**:
- `.claude/rules/closing-commit-purity.md` — added "Common rationalizations" table (3 excuses/rebuttals)
- `.claude/rules/git-staging.md` — added "Common rationalizations" table (3 excuses/rebuttals)
- `.claude/rules/docs-in-impl.md` — added "Common rationalizations" table (3 excuses/rebuttals)
- `project/CODING_STANDARDS.md` — added "Bug fixes start with a failing test" bullet (Prove-It pattern)
- `.claude/rules/debugging.md` — NEW always-loaded rule: Reproduce → Localize → Reduce → Fix → Guard, plus Never/Always/Why/Rationalizations/Enforcement
- `project/CLAUDE.md` — registered `debugging` under "Enforceable rules" (required `.claude/.unlock-safety` briefly; flag removed before commit)
- `docs/claude-code-setup.md` — added `debugging.md` to the tree diagram and updated "four always-loaded rules" → "five"
**Commands Run**: `make check` (1357 tests passing, coverage 92.25%)
**Result**: Success — all tasks complete; rule set now numbers 5 always-loaded + 3 path-scoped.
**Next**: Close and merge.

**Reflection** (via pre-close-verifier):
- Verdict: REQUEST CHANGES → addressed
- Coverage: 8/8 tasks addressed; all rationalizations tables present, Prove-It bullet present, debugging rule self-consistent with cross-refs resolving cleanly, registration in `project/CLAUDE.md` and `docs/claude-code-setup.md` confirmed.
- Shortcuts found: none. No TODO/FIXME, no stale `# type: ignore`, no copy-paste duplication. Rationalizations tables match the two-column format already used in `project/issues/CLAUDE.md`.
- Scope drift: none. Diff matches Resolved intent exactly.
- Stragglers: one — `project/lessons-learned.md:37` still said *"Four universal rules stay always-loaded"*. Fixed in commit `d42e20a` by dropping the count entirely (future-proof against further rule changes). `make check` re-run after the fix.
- Tone audit: clean. No agent-skills jargon ("anti-rationalization", "Stop-the-Line", OWASP) leaked into Marcel's voice.
- Unlock-flag audit: `.claude/.unlock-safety` was created and removed within the impl commit window; not present on disk, not referenced in any committed file.

## Lessons Learned

### What worked well
- **Research-first framing avoided a wholesale import.** The user asked a question ("is any of this useful?"), not for an implementation. Parallel Explore agents surveying both repos in one turn produced enough material to write an honest tradeoff assessment rather than a shopping list, and the resulting scope was three tiny documentation edits instead of a new skills subtree. Default to answering the actual question, not the ambitious version of it.
- **Marcel's existing rule voice is strong enough to borrow the pattern without the jargon.** The agent-skills "Common Rationalizations" tables are excellent, but the agent-skills phrasing ("anti-rationalization", "Stop-the-Line") would have stuck out. Translating each table into Marcel's `| Excuse | Reality |` format — which was already in use in `project/issues/CLAUDE.md` — meant the tables read as native on the first pass; the pre-close-verifier confirmed zero tone drift.
- **Pre-close-verifier caught a context-loaded lessons-learned straggler the writer would have shipped.** The verifier flagged `project/lessons-learned.md:37` ("Four universal rules stay always-loaded") because it's always-loaded and a future reader would trust the count. The straggler grep the writer ran earlier had excluded `project/lessons-learned.md` by instinct. **Lesson:** when changing something that has a count anywhere, always-loaded files are the first place to check — they quietly become the source of truth for anyone who reads them later.

### What to do differently
- **Do not freeze live counts into always-loaded prose.** The stale phrase in `lessons-learned.md` was introduced one issue ago with *"Four universal rules stay always-loaded; the three domain-specific ones only load when relevant"* — a factual statement that aged out the moment a rule was added. Fixed by rewording to *"Universal rules stay always-loaded; domain-specific ones only load when relevant"*. Going forward: when writing lessons-learned entries, phrase observations qualitatively ("universal vs. domain-specific") rather than with raw counts that will drift.
- **`project/CLAUDE.md` editing requires the unlock flag even for one-line rule registrations.** The guard-restricted hook correctly blocked the edit; the unlock/edit/remove cycle worked but added one extra turn. **Lesson:** when planning a rule addition, bundle the `project/CLAUDE.md` registration together with all other edits, touch the unlock flag once, and delete it before staging so the impl commit cannot accidentally include it.

### Patterns to reuse
- **Adapt, don't import, when adopting patterns from another project's "skills" repo.** The agent-skills SKILL.md files are structured as standalone executable workflows; Marcel's rules are short, enforceable, and cross-referenced by subagents. Lifting the *structure* (rationalizations table, anti-excuse framing) without lifting the *words* produces additions that feel native and don't create two competing sources of truth.
- **Straggler grep must include `project/lessons-learned.md`.** The file is always-loaded and can easily contain frozen factual claims about the codebase state. Add it to the default grep scope in [docs-in-impl.md](../.claude/rules/docs-in-impl.md) whenever you change something referenced there. The pre-close-verifier is currently the safety net — don't rely on it.
- **Unique-string Edits on shared prose files beat line-based Writes.** The rationalizations-table insertions used `Edit` with a distinctive anchor (the `## Enforcement` header) as `old_string`, which means the edit is robust even if other parts of the rule file change between read and write. For any cross-file batch edit, anchor on the most locally unique string, not the first matching line.
