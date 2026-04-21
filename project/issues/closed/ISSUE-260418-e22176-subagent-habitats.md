# ISSUE-e22176: Subagent habitats (fifth habitat)

**Status:** Closed
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Subagent definitions (...) Are they a fifth habitat (`zoo/agents/`), or do they belong inside the habitat that uses them?" → User confirmed: **fifth habitat**.

**Resolved intent:** Subagents are the fifth marcel-zoo habitat type alongside integrations, skills, channels, and jobs. Unlike integrations and channels, they're reusable across habitats — a news summarizer subagent might be invoked from a job, an integration, and a skill — so they earn their own top-level directory rather than being folded into whichever habitat happens to invoke them first.

## Description

Today subagent definitions live at [src/marcel_core/defaults/agents/](../../src/marcel_core/defaults/agents/) and are seeded into `~/.marcel/agents/` on first boot. The loader at [agents/loader.py](../../src/marcel_core/agents/loader.py) already reads from `<data_root>/agents/` — runtime-side, subagents are almost already a habitat.

This issue does two things:

1. **Stop seeding** subagents from `defaults/agents/`. Move the files from the source tree into `~/.marcel/agents/` as the authoritative location. Runtime behavior unchanged (loader already reads data root), but the kernel no longer ships example agents.
2. **Document the agent habitat pattern.** Each subagent is a single markdown file today (`.md` with frontmatter per Claude Code's subagent format). The habitat convention here is "one markdown file = one subagent" — no wrapper directory unless the subagent grows resources (prompt fragments, reference data). If/when that happens, promote to `zoo/agents/<name>/agent.md` + resources. For now, files-at-the-root is fine.

Question to resolve during implementation: do agents need a plugin API surface at all? They don't execute Python code — they're pure prompts loaded by the kernel's agent runner. So no `marcel_core.plugin.agents` is needed; they're just markdown files loaded from a conventional path. This is the simplest habitat type.

Note: this issue moves **runtime subagents** (explore, default agent definitions under `defaults/agents/`). It does **not** touch [.claude/agents/](../../.claude/agents/) — those are Claude Code developer-mode agents (`pre-close-verifier`, `code-reviewer`, `security-auditor`) and live inside the repo as part of the dev toolchain. They're a different thing with the same name; leave them alone.

## Tasks

- [✓] Audit [src/marcel_core/defaults/agents/](../../src/marcel_core/defaults/agents/) — list every file + what it does.
- [✓] For each file: move from `src/marcel_core/defaults/agents/<name>.md` → `<MARCEL_ZOO_DIR>/agents/<name>.md`.
- [✓] Remove the `_DEFAULTS_DIR / 'agents'` seeding block from [src/marcel_core/defaults/__init__.py](../../src/marcel_core/defaults/__init__.py).
- [✓] Verify: fresh boot with empty `~/.marcel/agents/` does not spawn a default — user has to install marcel-zoo (or hand-drop an agent file).
- [✓] Verify: [agents/loader.py](../../src/marcel_core/agents/loader.py) still discovers files from the data-root path. (Extended to also discover from `<MARCEL_ZOO_DIR>/agents/` — see Implementation Log.)
- [✓] Tests: any tests that loaded subagents from `defaults/agents/` update to either (a) a fake agent fixture under a tmp data root or (b) point at the migrated zoo file. Kernel loader tests must not depend on real agent content.
- [✓] Docs: new section in `docs/plugins.md` (or `docs/subagents.md`) describing the agent habitat pattern — one markdown file at `<data_root>/agents/<name>.md`, frontmatter per Claude Code subagent format, promoted to a directory if resources are needed.

## Relationships

- Depends on: ISSUE-3c87dd (for consistency with other habitat pattern documentation; technically could ship earlier — no plugin-API coupling)
- Blocks: ISSUE-63a946 (zoo repo extraction)

## Implementation Log

### 2026-04-21 — Subagents as fifth habitat

Moved the three bundled subagents (`explore`, `plan`, `power`) out of the kernel and into marcel-zoo at `<MARCEL_ZOO_DIR>/agents/`. The kernel now ships zero subagents — consistent with integrations, skills, channels, and jobs.

**Scope decision (loader):** the issue called for "no loader change" on the assumption that data-root reads alone were enough. In practice, once the three bundled files leave the kernel, a fresh install with `MARCEL_ZOO_DIR` set but empty `~/.marcel/agents/` would have no subagents at all — a silent regression. Extended `agents/loader.py` with `_agent_dirs()` mirroring `skills/loader.py`'s `_skill_dirs()`: zoo first, data root last, data root wins on name collision. This is the minimum change to honour the "fifth habitat" framing; without it, the habitat model is half-shipped. Same cold-read pattern, same idempotency, same `is_dir()` skip.

**Files touched:**
- `src/marcel_core/agents/loader.py` — added `_agent_dirs()`, rewrote `load_agents()` to walk both sources and tag `source='zoo'|'data'`.
- `src/marcel_core/agents/__init__.py` — docstring updated to describe the two-source discovery.
- `src/marcel_core/defaults/__init__.py` — deleted the 15-line agents-seeding block (the final kernel-bundled content for subagents). Module docstring tightened.
- `src/marcel_core/defaults/agents/{explore,plan,power}.md` — deleted.
- `<MARCEL_ZOO_DIR>/agents/{explore,plan,power}.md` — created (byte-identical copies of the deleted kernel files).
- `tests/agents/test_loader.py` — replaced `TestDefaultsSeeded` with `TestZooHabitat` (zoo discovery + data-root-overrides-zoo coverage); added `marcel_zoo_dir` isolation to the `agents_root` fixture so a populated zoo on the test machine cannot leak in.
- `docs/plugins.md` — new "Subagent habitat" section + cross-link to `subagents.md`.
- `docs/subagents.md` — "Default subagents" section now points at `<MARCEL_ZOO_DIR>/agents/`; "Agent definition files" lists both sources.
- `docs/model-tiers.md` — `power` agent description updated to reference the zoo source.
- `Dockerfile` — comment tightened: kernel ships zero subagents, only channels + routing.yaml still seed.

**Verification:** `make check` green (1340 pass, 91.30% coverage). `pre-close-verifier` run: APPROVE after straggler fixes (Dockerfile comment and `defaults/__init__.py` docstring — shipped in the final `🔧 impl:` commit before close per [docs-in-impl](../../.claude/rules/docs-in-impl.md)).

**Reflection** (via pre-close-verifier):
- Verdict: REQUEST CHANGES → addressed in the final `🔧 impl:` commit
- Coverage: 7/7 tasks addressed
- Shortcuts found: none
- Scope drift: none (loader extension is required scope, not creep — verified against the skill loader pattern)
- Stragglers: `Dockerfile:35-38` and `src/marcel_core/defaults/__init__.py:1` — both fixed in the `34c5d2f` final impl commit

## Lessons Learned

### What worked well

- **Mirroring the skill loader made the agent-loader extension trivial.** `_skill_dirs()` was literally the reference; one search-and-replace produced `_agent_dirs()`. When a new habitat type lands, the right first question is "which existing loader does this resemble?" and the answer costs ten minutes of work instead of an hour of invention.
- **Issue-file framing has inertia.** The issue said "loader should need no change" — that was written when the plan was "move files, keep data-root as sole source". Re-reading the issue after migration surfaced that the framing didn't match the "fifth habitat" goal. Don't treat the initial issue scope as sacred: when the resolved intent implies more, update scope and say so explicitly in the Implementation Log. The verifier caught this and agreed it was required scope, not drift.

### What to do differently

- **Straggler grep before first commit, not after.** The pre-close-verifier caught a `Dockerfile` comment and a stale docstring — both could have been caught by running the straggler grep (`grep -rn "seed.*agent\|defaults/agents"`) before the first impl commit. Cost one extra commit, but the pattern is: every issue that renames/removes a concept, run the grep immediately after the change, not at verifier time.

### Patterns to reuse

- **Habitat isolation in test fixtures.** The `agents_root` fixture now clears `settings.marcel_zoo_dir` in addition to `marcel_data_dir`. Any test fixture that isolates one habitat source must isolate *both* sources, otherwise a populated zoo on the dev machine leaks in. Apply this to future habitat types (agents was the third to adopt the pattern, after skills and jobs).
- **`source='zoo'|'data'` tag on loaded docs.** Plumb this through every habitat loader — it's cheap, and it gives both tests and future tools a way to say "where did this come from?" without re-walking the filesystem.
