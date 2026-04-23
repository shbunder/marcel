# ISSUE-a0840f: Issue workflow — make issues a self-sufficient workplan

**Status:** WIP
**Created:** 2026-04-23
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** workflow, docs, tooling

## Capture
**Original request:** Improve issue-workflow plan quality by making the issue file a self-sufficient Jira-style workplan, inspired by clawcode's plan mode but without the per-turn context overhead. Four changes: (1) Add `## Implementation Approach` section to project/issues/TEMPLATE.md with three subsections — Files to modify, Existing code to reuse (with paths), Verification steps — so every issue carries a concrete plan the reviewer can read. (2) Add the heuristic "never ask what reading the code can answer" as a one-line principle in .claude/skills/new-issue/SKILL.md's Step 4. (3) Add a `plan-verifier` subagent (mirrors pre-close-verifier) invoked at the open/→wip/ transition for non-trivial issues, checking Implementation Approach is populated with real file paths + a verification story — skipped for trivial work. (4) Wire Claude Code's plan mode as an opt-in escape hatch in /new-issue for genuinely ambiguous tasks (multi-file / architectural / user says "plan this") — on ExitPlanMode approval, transcode plan contents into the Implementation Approach section; plan file itself is throwaway. Context discipline constraints: no new per-turn reminder hooks (the UserPromptSubmit WIP reminder already exists), template changes go in TEMPLATE.md not CLAUDE.md (lazy-loaded vs every-session), and start with one-shot writes before adding any new issue-task CLI subcommands. Land in order 2 → 4 → 3 → 1 (template first because it carries most of the value, then cheap heuristic, then verifier once schema exists, then plan-mode wiring last as optional enhancement).

**Follow-up Q&A:**
- *What role does the issue workflow serve?* — Jira-style ticketing + workplan + audit trail. The committed issue file is the source of truth; reviewers read closed issues to follow work. Planning conversations are ephemeral — only what lands in the issue file counts.
- *Should we replicate clawcode's full interview loop?* — No. The interview loop is context-heavy (Explore agents, multiple AskUserQuestion rounds) and only earns its cost for genuinely ambiguous tasks. For the majority of Marcel's issues, a well-structured template + a cheap heuristic is sufficient.
- *Ordering rationale?* — Template first because it defines the schema everything else fills in; heuristic second because it's one line; verifier third because it needs the schema to check against; plan-mode wiring last because it's the optional escape hatch that transcodes into the same schema.

**Resolved intent:** Today the issue file is a title + a speculative task list — no structured plan, no file paths, no verification story. Reviewers reading closed issues can't see *how* the work was done, only that it happened. This issue adds a concrete `Implementation Approach` schema to the template, a cheap heuristic to stop needless questions, a lightweight verifier at the open→wip gate, and a conservative escape hatch into Claude Code's native plan mode for genuinely ambiguous work. Net effect: every issue becomes a readable workplan; hard issues get plan-mode treatment without paying its cost on easy ones.

## Description

The issue-workflow is Marcel's ticketing system — each task has a file, each file becomes a git-tracked artifact that reviewers can read long after the work ships. Clawcode's plan mode (explored in this conversation) has a richer planning protocol but pays for it with per-turn context overhead (system-reminder attachments, multiple agent rounds, AskUserQuestion loops). Marcel should adopt the *output shape* (structured plan with files + reuse + verification) without adopting the *protocol*.

### Why this order

Concrete, file-scoped changes ship from smallest footprint outward:

1. **TEMPLATE.md** — single-file change, zero per-turn cost, every future issue benefits. Defines the schema the other changes depend on.
2. **Heuristic in `/new-issue`** — one line. Free win.
3. **`plan-verifier` subagent** — new file in `.claude/agents/`, invoked at open→wip transition. Bounded context cost (one subagent call per non-trivial issue), mirrors existing `pre-close-verifier` pattern.
4. **Plan-mode wiring** — conditional branch in `/new-issue` that calls `EnterPlanMode` for multi-file / architectural / "plan this" requests. On `ExitPlanMode` approval, transcode plan contents into the Implementation Approach section of the issue file. Plan file in `~/.claude/plans/` is throwaway.

### Context discipline (non-negotiable)

- **No new per-turn reminder hooks.** The `UserPromptSubmit` hook already nudges on WIP files. Adding a second per-turn reminder would be the exact overhead we're trying to avoid.
- **Template changes go in TEMPLATE.md, not CLAUDE.md.** TEMPLATE is lazy-loaded when the skill reads it; CLAUDE.md is loaded every session.
- **One-shot writes before new CLI subcommands.** The Implementation Approach section is written once at the first `🔧 impl:` commit (or at `/new-issue` time when plan mode fires). Only add an `issue-task approach ...` subcommand later if we actually see token cost from rewrites.

### Trigger rules for plan mode (task 4)

Conservative, explicit, easy to opt out of:

- User says "plan this" / "plan first" / "/plan" → plan mode
- Request mentions new architecture or affects >3 files (agent judgment) → plan mode
- Otherwise → current flow (straight to issue file)

The trigger lives in `/new-issue` Step 4 (before writing the issue file). When plan mode fires, the agent uses it as the thinking space and the skill resumes the issue-write from the approved plan's contents.

## Tasks
- [✓] Task 1 — Add `## Implementation Approach` section to [project/issues/TEMPLATE.md](../TEMPLATE.md) with three subsections: **Files to modify** (bulleted paths), **Existing code to reuse** (bulleted `function/symbol — path:line — why`), **Verification steps** (bulleted commands or manual checks). Place it after `## Description`, before `## Tasks`. Also add a short explainer block below the template code showing how to fill it in.
- [✓] Task 2 — Add a one-line heuristic to [.claude/skills/new-issue/SKILL.md](../../../.claude/skills/new-issue/SKILL.md) Step 4: **"Never ask the user what reading the code can answer — explore first, ask only about requirements, tradeoffs, or preferences."** Place it as a bullet near the top of Step 4 so it's read before the template-fill instructions.
- [ ] Task 3 — Create [.claude/agents/plan-verifier.md](../../../.claude/agents/plan-verifier.md) mirroring [.claude/agents/pre-close-verifier.md](../../../.claude/agents/pre-close-verifier.md)'s structure. Tools: `Read`, `Grep`, `Glob`, `Bash` (read-only). Checks: (a) `## Implementation Approach` exists and all three subsections are populated with non-placeholder content; (b) **Files to modify** paths actually exist in the repo (or are plausibly new paths within existing directories); (c) **Verification steps** contain at least one executable check (command or explicit manual procedure). Returns a structured verdict (proceed / block / warn).
- [ ] Task 4 — Wire `plan-verifier` invocation into [.claude/skills/new-issue/SKILL.md](../../../.claude/skills/new-issue/SKILL.md) at the open→wip transition (after the `🔧 impl:` commit that moves the file). Skip for trivial issues (label: `docs` only, or user explicitly said "trivial"). The verifier's verdict is advisory — block only on missing section entirely, warn on weak content.
- [ ] Task 5 — Add plan-mode escape hatch to [.claude/skills/new-issue/SKILL.md](../../../.claude/skills/new-issue/SKILL.md). New step between Step 3 (slug) and Step 4 (write file): **"If the request is ambiguous, multi-file, or the user said 'plan this': call `EnterPlanMode` and run the planning loop. On `ExitPlanMode` approval, read the resulting plan file from `~/.claude/plans/` and transcode its contents into the Implementation Approach + Description + Tasks sections of the issue template."** Explicit trigger rules (see Description).
- [ ] Task 6 — Update [project/issues/CLAUDE.md](../CLAUDE.md) with a one-paragraph reference to the new Implementation Approach section (what it is, when it's filled in). No rule changes — just pointing readers at the schema. Keep it terse.
- [ ] Task 7 — Manual verification: create a throwaway test issue via `/new-issue` (trivial) and another via `/new-issue` with "plan this" (plan-mode path). Confirm the resulting issue files have populated Implementation Approach sections. Delete the test issues before the `✅ close` commit.

## Relationships
<!-- No dependencies inferred from open/wip issues -->

## Comments
<!-- Use for decisions, blockers, and discussion -->

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 08:35 - LLM Implementation
**Action**: Add one-line research-first heuristic to /new-issue Step 4
**Files Modified**:
- `.claude/skills/new-issue/SKILL.md`

### 2026-04-23 08:28 - LLM Implementation
**Action**: Add Implementation Approach section to TEMPLATE.md (Files to modify / Code to reuse / Verification steps) + explainer block below
**Files Modified**:
- `project/issues/TEMPLATE.md`
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
