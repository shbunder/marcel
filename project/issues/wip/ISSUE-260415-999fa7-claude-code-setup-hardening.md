# ISSUE-999fa7: Claude Code setup hardening

**Status:** Open
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** High
**Labels:** tooling, safety, workflow

## Capture

**Original request:** claude-code-setup-hardening — execute the audit follow-ups: PreToolUse safety hook blocking writes to CLAUDE.md / auth / config; prune settings.local.json to broad rules; create .claude/agents/ with pre-close-verifier, code-reviewer, security-auditor (adapted from ~/repos/agent-skills); wire pre-close-verifier into finish-issue; trim CODING_STANDARDS.md (drop generic Python tips); tighten root CLAUDE.md (add Commands section, drop Two modes + runtime-skill overview); rotate lessons-learned.md into current + archive with a maintenance rule; change FEATURE_WORKFLOW to grep lessons-learned instead of reading the whole file; add a statusline script; ship docs/claude-code-setup.md

**Follow-up Q&A:**
- Q: scope — incremental or hard brush? A: hard brush, fundamentally improve.
- Q: reuse from `~/repos/agent-skills`? A: yes, adapt the reviewer/test/security agent personas and anti-rationalization patterns where they fit Marcel's workflow.
- Q: more rules files? A: prefer Marcel's existing CLAUDE.md hierarchy (progressive disclosure) over inventing an untested `.claude/rules/` layout. Keep Marcel-specific coding rules near the code they govern.

**Resolved intent:** Marcel's Claude Code setup drifted since ISSUE-079 — the self-modification safety rules are advisory-only (Claude can still overwrite `CLAUDE.md`, `auth/`, `config.py` with no guard), `settings.local.json` has accreted 350+ one-shot permission entries, `CODING_STANDARDS.md` is mostly generic Python trivia already covered by `ruff`/`mypy`, the verification phase in `finish-issue` runs inline in the main context instead of via a fresh-context subagent, and `lessons-learned.md` is on track to blow the context window. This issue executes a hard-brush cleanup: add real guardrails via a PreToolUse hook, slim the always-loaded docs, introduce the first project subagents (adapted from `~/repos/agent-skills`), rotate `lessons-learned.md`, and document the resulting setup.

## Description

Follow-up to the audit performed in the conversation immediately preceding this issue. The audit compared Marcel's current Claude Code configuration against the official Best Practices guide and against `~/repos/agent-skills`, and identified ten concrete gaps ranked P0–P3. The user accepted all of them and asked for additional work on `lessons-learned.md` maintenance and on importing patterns from `agent-skills`.

Safety is the headline item. Marcel is a self-modifying agent whose defining feature is rewriting its own code. The rule *"Auth logic, core config, and safety rules (including these CLAUDE.md files) are off-limits unless the user explicitly grants permission"* lives in `CLAUDE.md` as advisory text — it has no enforcement. A PreToolUse hook that blocks `Edit`/`Write` to those paths unless a flag file is present makes the rule real. This is the single highest-value change.

Beyond safety, the setup cleanup is about **context economy**: the always-loaded footprint (root CLAUDE.md + what it transitively pulls) should hold only things Claude cannot derive from the code itself, and verification-heavy workflows like `finish-issue` should delegate to subagents so their file-reading doesn't pollute the main conversation.

## Tasks

- [ ] Add `PreToolUse` hook to `.claude/settings.json` that blocks `Edit`/`Write`/`NotebookEdit` against `CLAUDE.md` (any depth), `src/marcel_core/auth/**`, `src/marcel_core/config.py`, and `.env*`, unless `.claude/.unlock-safety` exists
- [ ] Write `.claude/hooks/guard-restricted.sh` (the script the hook invokes); read tool input from stdin, check `tool_input.file_path`, exit 2 with a friendly message on violation
- [ ] Document the unlock flag workflow (create flag, make change, delete flag) in `docs/claude-code-setup.md`
- [ ] Prune `.claude/settings.local.json` — drop historical one-shot entries, keep ~20 broad allow rules (`Bash(git *)`, `Bash(make *)`, `Bash(ls *)`, `Bash(grep *)`, `Bash(python3 -c *)`, etc.); preserve `additionalDirectories`
- [ ] Create `.claude/agents/` directory with three subagent files:
  - [ ] `pre-close-verifier.md` — runs the shortcut/scope-drift/grep-for-stragglers verification from `finish-issue` Step 6 in a fresh context; returns a structured report
  - [ ] `code-reviewer.md` — adapted from `~/repos/agent-skills/agents/code-reviewer.md`, retitled to reference Marcel's conventions (pydantic-ai, flat files, skill system) rather than generic framework references
  - [ ] `security-auditor.md` — adapted from agent-skills, scoped to Marcel's actual attack surface (credential storage under `~/.marcel/users/`, Telegram webhook secret, API token, self-modification restart flag)
- [ ] Update `.claude/skills/finish-issue/SKILL.md` Step 6 to delegate shortcut/scope-drift checks to the `pre-close-verifier` subagent via the `Agent` tool; main context only records the verdict
- [ ] Rewrite `project/CODING_STANDARDS.md` — cut to Marcel-specific rules only (drop items already enforced by `ruff`/`mypy`); target ≤15 lines
- [ ] Tighten root `CLAUDE.md`:
  - [ ] Add a `## Commands` section with `make serve`/`make check`/`make test`/`make cli-dev` and the dev vs prod port distinction
  - [ ] Move "Two modes, two instruction sets" explanation out (or reduce to one sentence)
  - [ ] Move "Skill system overview" paragraph to `src/marcel_core/skills/CLAUDE.md` (on-demand child) or `docs/skills.md`
  - [ ] Add a line pointing at `.claude/agents/` so the agent knows subagents exist
- [ ] Rotate `project/lessons-learned.md`:
  - [ ] Move everything older than the 10 most recent entries into `project/lessons-learned-archive.md`
  - [ ] Add a maintenance rule at the top: "Keep ≤10 active entries. When adding a new one, move the oldest to the archive. Grep the archive when relevant."
- [ ] Update `project/FEATURE_WORKFLOW.md` Step 1: change "read `project/lessons-learned.md`" → "`grep -i '<keyword>' project/lessons-learned.md project/lessons-learned-archive.md` for terms from the resolved intent"
- [ ] Add `.claude/statusline.sh` and wire it up in `.claude/settings.json` — show branch + active-issue count + uncommitted-file count
- [ ] Write `docs/claude-code-setup.md` — covers hook layout, safety flag workflow, subagent roster, lessons-learned rotation policy; register it in `mkdocs.yml`
- [ ] Run `make check` — all green
- [ ] Close via `/finish-issue`

## Subtasks

(none — tasks are flat)

## Relationships

- Related to: [[ISSUE-079-claude-code-setup-redesign]] — previous round of setup work; this issue extends it based on the best-practices audit.
- Related to: [[ISSUE-0554d9-parallel-agent-worktrees]] — the worktree work just shipped; `finish-issue` changes here must not break worktree-mode closing.

## Comments

### 2026-04-15 - LLM
Audit source: conversation transcript prior to issue creation. User explicitly approved all ten P0–P3 items from the audit, plus: (a) clean up and maintain `lessons-learned.md`, (b) consider more rules files, (c) reuse what fits from `~/repos/agent-skills`. Decision on (b): keep Marcel's CLAUDE.md hierarchy — it already provides on-demand loading via child CLAUDE.md files — rather than bet on untested `.claude/rules/` support.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
