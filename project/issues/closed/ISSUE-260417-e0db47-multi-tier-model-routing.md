# ISSUE-e0db47: Multi-tier model routing with per-session classifier

**Status:** Closed
**Created:** 2026-04-17
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature

## Capture

**Original request:** multi-tier model routing with per-session classifier, per-tier OpenAI fallback, and externalized EN+NL keyword config. Plan: /home/shbunder/.claude/plans/i-want-to-implement-whimsical-neumann.md

**Follow-up Q&A:**
- Ambition of the router? → Declared tiers + keyword-config heuristic. No ML classifier, no external routing SaaS.
- Quality-based re-runs? → Out of scope. Error cascade stays; quality cascades not worth the cost for a family assistant.
- One global cross-cloud backup or per-tier? → **Per-tier**. Each tier has its own OpenAI fallback.
- Session-scoped or per-turn? → **Per-session**. Classify once at session start (fast vs standard only). Frustration can bump mid-session.
- POWER in skills? → Yes. Skills can declare `preferred_tier: power` (e.g. `developer`). POWER is never auto-picked by the classifier.
- Job pin vs. skill `preferred_tier`? → Job pin wins — jobs never consult the classifier or session tier.
- Mid-session skill with different tier? → Per-turn override only; does not mutate `channel_tiers`.
- Backward compat for `marcel_backup_model`? → No — remove cleanly, flag as breaking.

**Resolved intent:** Marcel already has a 4-tier fallback chain but a) no FAST (Haiku-class) tier, b) one global cross-cloud backup shared by all tiers, c) no automatic tier selection. This issue adds a FAST tier, gives each tier its own per-tier OpenAI fallback, and introduces a cheap, transparent, keyword-based classifier that picks fast-vs-standard **once per session**. POWER stays opt-in (skills or subagents declare it explicitly). Frustration triggers bump fast→standard mid-session. Routing keywords live in an editable YAML under `~/.marcel/` with separate EN/NL lists. No ML, no external routing service, no new runtime dependency.

## Description

Full plan at [`/home/shbunder/.claude/plans/i-want-to-implement-whimsical-neumann.md`](/home/shbunder/.claude/plans/i-want-to-implement-whimsical-neumann.md). Summary:

**Shape of the change:**
1. Add `Tier.FAST`; remove `Tier.BACKUP` and the `tier:backup` sentinel (breaking).
2. Per-tier env vars: `marcel_fast_model` / `marcel_fast_backup_model` / `marcel_standard_backup_model` / `marcel_power_backup_model`. `marcel_backup_model` is removed.
3. `build_chain(*, tier: Tier, mode=...)` returns `[primary, per-tier backup?, local fallback?]`.
4. New `src/marcel_core/harness/tier_classifier.py`: loads `~/.marcel/routing.yaml` (mtime-cached), `classify_initial_tier`, `detect_frustration`, `maybe_bump_tier`. Broken YAML → baked-in defaults.
5. `UserSettings.channel_tiers: dict[str, str]` persists the session tier; reset on idle-summarize.
6. `SkillDoc.preferred_tier: Literal['fast','standard','power'] | None` — per-turn override, does **not** mutate session state.
7. Precedence in `stream_turn`: **job pin > subagent `model:` > skill `preferred_tier` > session tier (classifier + frustration bump)**.
8. Frustration regex seeded from clawcode's `matchesNegativeKeyword` + Dutch equivalents. Frustration only bumps fast → standard (POWER is never reached via frustration).

**Explicitly not in scope:** ML/embedding routers, external routing SaaS (OpenRouter-auto / LiteLLM-proxy / Not Diamond), quality-based re-runs / self-escalation tools, classifier auto-picking POWER, per-turn re-classification, language autodetection (both EN and NL patterns run together).

**Clean-code discipline (user flagged complexity):**
- Classifier module stays <~100 lines; one function per concern; regex patterns come from config, not hardcoded.
- No new abstractions unless at least two call sites need them.
- No backwards-compat shims for `marcel_backup_model` — delete it and note the breaking change.
- No speculative generality (e.g. no pluggable-classifier protocol; the YAML is the extension point).
- Precedence hierarchy lives in one place (`stream_turn` selection helper) — resist scattering tier-resolution logic across the codebase.

## Tasks

- [✓] Config + chain: add `Tier.FAST`; new env vars (`marcel_fast_model`, `marcel_{fast,standard,power}_backup_model`); remove `marcel_backup_model` and `Tier.BACKUP`; rewrite `build_chain(tier=)` for per-tier `[primary, backup?, local?]`; update `resolve_tier_sentinel` (fast/standard/power only)
- [✓] Routing config: seed `src/marcel_core/defaults/routing.yaml` with EN+NL `fast_triggers` / `standard_triggers` / `frustration_triggers` (frustration from clawcode regex); copy to `~/.marcel/routing.yaml` on first startup via existing defaults-seeding pattern
- [✓] Classifier module: new `src/marcel_core/harness/tier_classifier.py` — `load_routing_config` (mtime-cached, broken-YAML-resilient), `classify_initial_tier`, `detect_frustration`, `maybe_bump_tier`; <~100 lines total
- [✓] Session state: add `channel_tiers: dict[str, str]` to `UserSettings` with `load_session_tier` / `save_session_tier` helpers; reset hook wired into idle-summarize
- [✓] Skills: add `preferred_tier: Literal['fast','standard','power'] | None` to `SkillDoc`; parse from frontmatter; reject unknown values
- [✓] Precedence in `stream_turn`: single helper that walks **subagent → skill → session → classifier**; skill and subagent overrides are per-turn only (do not mutate `channel_tiers`); jobs pass explicit tier via `executor.py` and bypass the session/classifier path entirely
- [✓] Seed 2–3 defaults: `developer` skill with `preferred_tier: power`; one quick-lookup skill with `preferred_tier: fast`; verify the three tiers are exercised from day one
- [✓] Tests: per-tier `build_chain` assembly + `tier:backup` rejection; EN+NL classifier + YAML mtime reload + broken-YAML fallback; `UserSettings.channel_tiers` round-trip; `stream_turn` precedence (subagent > skill > session); skill `preferred_tier` does NOT mutate session state; frustration bump mutates session state; job pin ignores skill `preferred_tier`; frustration during STANDARD is a no-op (no auto-POWER)
- [✓] Docs: update `.env.example`, `docs/model-tiers.md`, `docs/local-llm.md`; add a `docs/routing.md` for `~/.marcel/routing.yaml`; register new pages in `mkdocs.yml` nav; note the breaking removal of `MARCEL_BACKUP_MODEL` in the Implementation Log

## Relationships

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-17 — Feature shipped in three impl commits

Commits on branch (ahead of `main`):

- `c84271e` — per-tier chain, EN+NL classifier, session tier storage
- `d33d18a` — session-tier resolution + precedence helper wired into `stream_turn`
- `4e75ea8` — seed tier-declaring skills, full docs, extra integration tests

**Shape of the change (what landed):**

1. **Config + chain.** `Tier.BACKUP` and `tier:backup` sentinel removed; `Tier.FAST` + `tier:fast` added. `src/marcel_core/config.py` now exposes `marcel_fast_model` / `marcel_{fast,standard,power}_backup_model`; `marcel_backup_model` is deleted (breaking change — see callout below). `build_chain(*, tier, primary=None, mode=...)` in `src/marcel_core/harness/model_chain.py` returns `[primary, per-tier backup?, local fallback?]`.
2. **Routing config.** `src/marcel_core/defaults/routing.yaml` seeded with EN+NL `fast_triggers`, `standard_triggers`, and `frustration_triggers` (the frustration list is adapted from clawcode's `matchesNegativeKeyword`). `src/marcel_core/defaults/__init__.py` copies it to `~/.marcel/routing.yaml` on first startup, same pattern as skills seeding.
3. **Classifier.** `src/marcel_core/harness/tier_classifier.py` is ~110 lines: `RoutingConfig`, `load_routing_config` (mtime-cached), `classify_initial_tier`, `detect_frustration`, `maybe_bump_tier`. Broken YAML falls back to the baked-in defaults path — a user edit cannot brick the router.
4. **Session state.** `UserSettings.channel_tiers: dict[str, str]` persists the session tier. Helpers: `load_channel_tier`, `save_channel_tier`, `clear_channel_tier`. Idle-summarize in `runner.py` clears the channel tier so the next message re-classifies.
5. **Skills.** `SkillDoc.preferred_tier: Literal['fast','standard','power'] | None` parsed from frontmatter; unknown values rejected with a clear error. Seeded: `developer` → `power`, `settings` → `fast`.
6. **Precedence.** Single helper `_resolve_turn_tier` in `runner.py` walks **subagent → active skill → session → classifier**. Among multiple active skills, POWER > STANDARD > FAST wins. Skill and subagent overrides are per-turn only and never mutate `channel_tiers`; only the classifier and the frustration bump do.
7. **Jobs.** `jobs/executor.py` calls `build_chain(tier=Tier.STANDARD, primary=job.model, mode='complete')` directly — jobs never consult the classifier, never look at `channel_tiers`, and ignore any skill `preferred_tier` invoked during a job.
8. **Frustration.** `maybe_bump_tier` only bumps FAST → STANDARD. A STANDARD session with frustration is a no-op (POWER is never reached by the classifier or by frustration, per plan).
9. **Docs.** `docs/model-tiers.md` rewritten for the three-tier ladder with per-tier backups; new `docs/routing.md` documents `~/.marcel/routing.yaml` shape, classification flow, and debugging misroutes; `docs/subagents.md` / `docs/local-llm.md` / `README.md` / `SETUP.md` updated; `mkdocs.yml` gained a `Session Routing` nav entry.

**Breaking change — `MARCEL_BACKUP_MODEL` removed.** The single shared backup is gone; migrate to the appropriate per-tier variable (almost always `MARCEL_STANDARD_BACKUP_MODEL`). `SETUP.md` and `docs/model-tiers.md` carry the migration callout. Subagent frontmatter with `model: backup` is rejected at load time with a warning pointing at the new tier names. The local admin's `.env.local` was updated in this issue.

**Tests:** 1421 passing, 91.80 % coverage, ruff + pyright clean. New suite `tests/harness/test_tier_classifier.py` (19 cases including EN + NL, mtime reload, broken-YAML fallback); `test_runner.py::TestResolveTurnTier` covers the precedence hierarchy end-to-end; integration tests verify idle reset and FAST-session chain assembly.

**Reflection** (via pre-close-verifier):
- **Verdict:** REQUEST CHANGES → addressed (4 stragglers fixed in `024f615`).
- **Coverage:** 8/8 tasks addressed — every task bullet maps to diffed code and at least one test.
- **Shortcuts found:** none. No TODO/FIXME/XXX, no bare excepts, no print debugging. The three `except Exception` blocks in `runner.py` are documented chain-advancement points that log and fall through to the next fallback entry.
- **Scope drift:** none. Nothing landed beyond the plan — no pluggable-classifier protocol, no per-turn reclassification, no language autodetect.
- **Stragglers (all fixed in `024f615`):**
  - `docs/subagents.md:100` — sentinel list still contained `backup` and was missing `fast`. Updated to `fast / standard / power / fallback`.
  - `tests/harness/test_runner.py:391` — comment `MARCEL_BACKUP_MODEL` → `MARCEL_STANDARD_BACKUP_MODEL`.
  - `tests/jobs/test_executor.py:342,381` — two stale docstrings renamed to `MARCEL_STANDARD_BACKUP_MODEL`.
- **Notes:** `.env.example` bullet resolved to a no-op — Marcel has `.env` and `.env.local` at repo root, no `.env.example`. Breaking-change callouts are documented in `SETUP.md` and `docs/model-tiers.md` instead. `tier_classifier.py` is 186 lines, not the ~100 estimated in the plan — still single-concern and readable.

**Admin action (this issue):** `.env.local` migrated — `MARCEL_BACKUP_MODEL` removed, replaced with `MARCEL_FAST_MODEL` / `MARCEL_FAST_BACKUP_MODEL` / `MARCEL_STANDARD_BACKUP_MODEL` / `MARCEL_POWER_BACKUP_MODEL`. The FAST tier is wired to Haiku + gpt-4o-mini; STANDARD and POWER keep their previous models with `openai:gpt-4.1` as the per-tier backup.

## Lessons Learned

### What worked well
- **Shipping all four layers (infra + state + classifier + observability) in one PR.** The plan flagged that cascade and router are complementary, not alternatives — splitting the work across two issues would have created a window where the new tiers existed without a picker, or the picker existed without a FAST tier to route to. Bundling meant every commit was individually deployable and the three `🔧 impl:` commits fell out naturally along a dependency order (chain → precedence → skills+docs).
- **Single precedence helper in one place.** `_resolve_turn_tier` is the only code that walks subagent → skill → session → classifier. The user explicitly flagged "resist scattering tier-resolution logic across the codebase" in the issue description, and the verifier confirmed it's called from exactly one site. This made the precedence matrix easy to unit-test (`TestResolveTurnTier` has eleven cases) and easy to reason about when adding the frustration bump.
- **Delegating pre-close verification to a fresh-context agent.** Main conversation was biased toward the code it just wrote; the `pre-close-verifier` agent found four `MARCEL_BACKUP_MODEL` stragglers that I had missed (docs/subagents.md sentinel table, plus three test docstrings). Twelve minutes of delegation caught twelve minutes of follow-up fixes that would otherwise have rotted as stale references.

### What to do differently
- **Plan-stated line targets are aspirational, not contractual.** `tier_classifier.py` was planned at "~80 lines, ~100 max" and shipped at 186 — the extra came from the `RoutingConfig` dataclass, three small helper functions (`_compile`/`_flatten`/`_parse`), mtime cache scaffolding, and docstrings. All justifiable, all single-concern. Takeaway: next time use line counts as a smell-check ("is this file sprawling?"), not as a budget that shapes design.
- **The ".env.example" task was vapourware.** The task list faithfully echoed the plan, which faithfully echoed a file that doesn't exist in this repo — Marcel uses `.env` (committed template) and `.env.local` (uncommitted secrets). Pre-issue grep for file existence when the task list names a specific path; don't inherit wording from the plan without verifying the path resolves.
- **Shared-backup `MARCEL_BACKUP_MODEL` removal was a bigger straggler footprint than expected.** The breaking change touched config, chain, delegate sentinels, subagent loader, docs, README, SETUP, `.env.local`, and multiple test docstrings. The verifier caught four stragglers in committed code; the straggler grep in the close workflow is now the only thing standing between "compile-clean rename" and "users confused by stale docs months later." Run it *before* the close commit, always.

### Patterns to reuse
- **Session-scoped state > per-turn state for stable decisions.** Classifying once per session and persisting in `UserSettings.channel_tiers` (not in-memory) survived the idle-summarize reset cleanly — reset is a single `clear_channel_tier(user_slug, channel)` call inside `summarize_if_idle`. Future stable-per-session decisions (language, verbosity, maybe even personality tone) can hang off the same `channel_*` pattern.
- **Baked-in defaults + user-override YAML with mtime reload.** `~/.marcel/routing.yaml` is user-editable, takes effect without a restart, and broken YAML falls back to `defaults/routing.yaml` so the router cannot be bricked. Same pattern would work for any other "config that non-technical users could theoretically edit but mostly won't": tone settings, default timezones, frustration sensitivity.
- **Classifier picks a subset of tiers.** Deliberately letting the classifier pick only FAST/STANDARD and keeping POWER subagent-only means no chatty conversation ever burns Opus quota. This "the automatic picker has narrower choices than the manual picker" pattern generalises: whenever an auto-picker exists alongside an explicit opt-in, the opt-in should be a strict superset and the auto-pick should be the cheap/safe subset.
