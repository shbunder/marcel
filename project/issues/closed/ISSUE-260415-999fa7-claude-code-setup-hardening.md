# ISSUE-999fa7: Claude Code setup hardening

**Status:** Closed
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

- [✓] Add `PreToolUse` hook to `.claude/settings.json` that blocks `Edit`/`Write`/`NotebookEdit` against `CLAUDE.md` (any depth), `src/marcel_core/auth/**`, `src/marcel_core/config.py`, and `.env*`, unless `.claude/.unlock-safety` exists
- [✓] Write `.claude/hooks/guard-restricted.py` (renamed from .sh — Python for reliable JSON parsing); read tool input from stdin, check `tool_input.file_path`, exit 2 with a friendly message on violation
- [✓] Document the unlock flag workflow (create flag, make change, delete flag) in `docs/claude-code-setup.md`
- [✓] Prune `.claude/settings.local.json` — dropped historical one-shot entries (352 → 43 lines), kept broad allow rules (`Bash(git *:*)`, `Bash(make:*)`, `Bash(python3:*)`, etc.); preserved `additionalDirectories`. File is gitignored so not in the diff.
- [✓] Create `.claude/agents/` directory with three subagent files:
  - [✓] `pre-close-verifier.md` — runs the shortcut/scope-drift/grep-for-stragglers verification from `finish-issue` Step 6 in a fresh context; returns a structured report
  - [✓] `code-reviewer.md` — adapted from `~/repos/agent-skills/agents/code-reviewer.md`, rewritten to reference Marcel's conventions (pydantic-ai, flat files, skill system, role-gated tools)
  - [✓] `security-auditor.md` — adapted from agent-skills, scoped to Marcel's actual attack surface (credential storage, Telegram webhook, API token, self-modification restart flag, role-gated tools, browser/web SSRF)
- [✓] Update `.claude/skills/finish-issue/SKILL.md` Step 6 to delegate shortcut/scope-drift checks to the `pre-close-verifier` subagent via the `Agent` tool; includes inline fallback when the Agent tool is unavailable
- [✓] Rewrite `project/CODING_STANDARDS.md` — cut to Marcel-specific rules only; 45 → 27 lines; added a "Not here" section explaining what belongs in `pyproject.toml` instead
- [✓] Tighten root `CLAUDE.md`:
  - [✓] Add a `## Commands` section with `make serve`/`make check`/`make test`/`make cli-dev` and the dev vs prod port distinction
  - [✓] Move "Two modes, two instruction sets" explanation out (reduced to one sentence)
  - [✓] Move "Skill system overview" paragraph out (now linked via `docs/skills.md`)
  - [✓] Add a line pointing at `.claude/agents/` so the agent knows subagents exist
- [✓] Rotate `project/lessons-learned.md`:
  - [✓] Moved 14 older entries into `project/lessons-learned-archive.md`, kept the 10 newest
  - [✓] Added a maintenance rule at the top of the active file
- [✓] Update `project/FEATURE_WORKFLOW.md` Step 1: changed "read `project/lessons-learned.md`" → "grep both files for keywords from the resolved intent"
- [✓] Add `.claude/statusline.sh` and wire it up in `.claude/settings.json` — shows branch + active-issue hash + uncommitted count + WIP count + safety-flag warning
- [✓] Write `docs/claude-code-setup.md` — covers hook layout, safety flag workflow, subagent roster, lessons-learned rotation policy; registered in `mkdocs.yml`
- [✓] Run `make check` — all green (1344 tests, 92.75% coverage, pre-commit hook enforced on every commit in this branch)
- [✓] Close via `/finish-issue`

## Subtasks

(none — tasks are flat)

## Relationships

- Related to: [[ISSUE-079-claude-code-setup-redesign]] — previous round of setup work; this issue extends it based on the best-practices audit.
- Related to: [[ISSUE-0554d9-parallel-agent-worktrees]] — the worktree work just shipped; `finish-issue` changes here preserve the worktree-aware merge logic.

## Comments

### 2026-04-15 - LLM
Audit source: conversation transcript prior to issue creation. User explicitly approved all ten P0–P3 items from the audit, plus: (a) clean up and maintain `lessons-learned.md`, (b) consider more rules files, (c) reuse what fits from `~/repos/agent-skills`. Decision on (b): keep Marcel's CLAUDE.md hierarchy — it already provides on-demand loading via child CLAUDE.md files — rather than bet on untested `.claude/rules/` support.

## Implementation Log

### 2026-04-15 19:00 - LLM Implementation
**Action**: Executed the full hardening plan across six `🔧 impl` commits.

**Files Modified**:
- `.claude/settings.json` — added statusLine + PreToolUse hook block
- `.claude/hooks/guard-restricted.py` (new) — blocks Edit/Write/NotebookEdit/MultiEdit on CLAUDE.md, src/marcel_core/auth/**, src/marcel_core/config.py, .env*; bypasses when `.claude/.unlock-safety` exists; fails open on malformed stdin
- `.claude/settings.local.json` — pruned 352 → 43 lines (gitignored, local only)
- `.claude/agents/pre-close-verifier.md` (new) — fresh-context verifier with shortcut/scope-drift/straggler checklist
- `.claude/agents/code-reviewer.md` (new) — 5-axis reviewer aware of Marcel's architecture
- `.claude/agents/security-auditor.md` (new) — scoped to Marcel's real attack surface
- `.claude/skills/finish-issue/SKILL.md` — Step 6 delegates to pre-close-verifier (with inline fallback); Step 10 now rotates lessons-learned into archive when the 10-entry cap is hit
- `.claude/statusline.sh` (new) — renders branch • issue-hash • uncommitted • wip • safety flag
- `.gitignore` — added `.claude/settings.local.json` and `.claude/.unlock-safety`
- `CLAUDE.md` — dropped "Two modes" verbosity and runtime-skill overview; added Commands section and subagent roster; 31 → 38 lines but now much denser in value
- `project/CLAUDE.md` — updated CODING_STANDARDS description to match the new trimmed scope
- `project/CODING_STANDARDS.md` — dropped generic Python tips already enforced by ruff/mypy; 45 → 27 lines
- `project/FEATURE_WORKFLOW.md` — Step 1 now greps lessons-learned instead of reading it
- `project/lessons-learned.md` — kept 10 newest entries, added maintenance rule, 441 → 218 lines
- `project/lessons-learned-archive.md` (new) — 14 older entries; read on demand via grep
- `docs/claude-code-setup.md` (new) — developer-facing documentation of the entire setup
- `mkdocs.yml` — registered new page in nav

**Commands Run**:
- `python3 .claude/hooks/guard-restricted.py` smoke-tested with 5 cases (block CLAUDE.md, allow source, block auth, block .env.local, allow with unlock flag present, malformed stdin, empty stdin)
- `make check` — passed after every commit via pre-commit hook (1344 tests, 92.75% coverage)
- `bash .claude/statusline.sh` — verified output format
- The safety hook activated naturally mid-session when editing `project/CLAUDE.md` — the unlock flag dance (`touch .claude/.unlock-safety` → edit → commit → `rm`) was exercised end-to-end

**Result**: All 15 top-level tasks complete. Safety hook validated under real conditions (it blocked an unprotected edit to `project/CLAUDE.md` until the unlock flag was set). `make check` green on every commit.

**Reflection** (inline — the `pre-close-verifier` subagent file exists but only activates at next session start, so verification ran inline using the same checklist as a fallback):
- **Verdict:** APPROVE
- **Coverage:** 15/15 tasks addressed
- **Shortcuts found:** 1 — bare `except Exception:` in `guard-restricted.py` main() was caught by the shortcut hunt; narrowed to `(json.JSONDecodeError, OSError, ValueError)` with a comment explaining the fail-open intent, committed as a separate impl commit (`92ea211`).
- **Scope drift:** none — every task maps to something the user asked for; the only unplanned commits were (a) splitting the main work across 5 impl commits for readability, and (b) the shortcut fix above.
- **Stragglers:** none — grepped `CODING_STANDARDS`, `lessons-learned.md`, `unlock-safety`, `guard-restricted`, `pre-close-verifier`, `lessons-learned-archive`, `settings.local.json`, `.env.local` across all tracked markdown; no references to old behavior remained outside the (expected) historical issue files under `project/issues/closed/`.
- **Marcel-specific checks:** no `git mv` happened after a Read (the rotation used Python `Path.write_text`, not a rename); `request_restart()` was not involved; no user data / system config cross-contamination; all new subagents documented; docs shipped in the second-to-last impl commit (not in close).

**Next**: New-session behavior needs a live test — the hook should block CLAUDE.md edits and the pre-close-verifier subagent should be invokable by name. Both are likely to work given the smoke tests passed, but we'll only know for sure on the next issue.
