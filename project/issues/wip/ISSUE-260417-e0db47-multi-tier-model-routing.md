# ISSUE-e0db47: Multi-tier model routing with per-session classifier

**Status:** Open
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

- [ ] Config + chain: add `Tier.FAST`; new env vars (`marcel_fast_model`, `marcel_{fast,standard,power}_backup_model`); remove `marcel_backup_model` and `Tier.BACKUP`; rewrite `build_chain(tier=)` for per-tier `[primary, backup?, local?]`; update `resolve_tier_sentinel` (fast/standard/power only)
- [ ] Routing config: seed `src/marcel_core/defaults/routing.yaml` with EN+NL `fast_triggers` / `standard_triggers` / `frustration_triggers` (frustration from clawcode regex); copy to `~/.marcel/routing.yaml` on first startup via existing defaults-seeding pattern
- [ ] Classifier module: new `src/marcel_core/harness/tier_classifier.py` — `load_routing_config` (mtime-cached, broken-YAML-resilient), `classify_initial_tier`, `detect_frustration`, `maybe_bump_tier`; <~100 lines total
- [ ] Session state: add `channel_tiers: dict[str, str]` to `UserSettings` with `load_session_tier` / `save_session_tier` helpers; reset hook wired into idle-summarize
- [ ] Skills: add `preferred_tier: Literal['fast','standard','power'] | None` to `SkillDoc`; parse from frontmatter; reject unknown values
- [ ] Precedence in `stream_turn`: single helper that walks **subagent → skill → session → classifier**; skill and subagent overrides are per-turn only (do not mutate `channel_tiers`); jobs pass explicit tier via `executor.py` and bypass the session/classifier path entirely
- [ ] Seed 2–3 defaults: `developer` skill with `preferred_tier: power`; one quick-lookup skill with `preferred_tier: fast`; verify the three tiers are exercised from day one
- [ ] Tests: per-tier `build_chain` assembly + `tier:backup` rejection; EN+NL classifier + YAML mtime reload + broken-YAML fallback; `UserSettings.channel_tiers` round-trip; `stream_turn` precedence (subagent > skill > session); skill `preferred_tier` does NOT mutate session state; frustration bump mutates session state; job pin ignores skill `preferred_tier`; frustration during STANDARD is a no-op (no auto-POWER)
- [ ] Docs: update `.env.example`, `docs/model-tiers.md`, `docs/local-llm.md`; add a `docs/routing.md` for `~/.marcel/routing.yaml`; register new pages in `mkdocs.yml` nav; note the breaking removal of `MARCEL_BACKUP_MODEL` in the Implementation Log

## Relationships

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
