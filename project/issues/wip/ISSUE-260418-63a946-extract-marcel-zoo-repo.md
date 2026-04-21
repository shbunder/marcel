# ISSUE-63a946: Delete defaults/ + extract marcel-zoo as a separate repo

**Status:** In Progress
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo, repo-extraction

## Capture

**Original request:** "The goal later is to put this into a separate git repository that 'installs' to the correct file. (...) marcel-zoo is the repository where all modular components for Marcel are added (jobs, integrations, skills, channels). It also contains /users/user_slug where for each user this folder should serve as the working directory for this user, here Marcel can store memories and create files (it will not be git tracked). There should also be tests in this repo."

**Resolved intent:** The final step. After ISSUE-3c87dd through ISSUE-a7d69a, `~/.marcel/` is already the canonical source for all habitats — integrations, skills, channels, jobs, agents. This issue (a) deletes the now-dead `src/marcel_core/defaults/` tree and its seeding mechanism, (b) promotes `~/.marcel/` into a standalone `marcel-zoo` git repository with its own `pyproject.toml`, tests, and CI, and (c) updates the installer / Docker build to clone the zoo into the data dir on first boot.

## Description

**What gets deleted from Marcel-core:**
- [src/marcel_core/defaults/__init__.py](../../src/marcel_core/defaults/__init__.py) — `seed_defaults()` function
- `src/marcel_core/defaults/skills/` — all default skill directories (should already be empty after ISSUE-bde0a1 + ISSUE-2ccc10)
- `src/marcel_core/defaults/agents/` — should already be empty after ISSUE-e22176
- `src/marcel_core/defaults/channels/` — should already be empty after ISSUE-7d6b3f
- `src/marcel_core/defaults/MARCEL.md` and `routing.yaml` — move to zoo root
- `src/marcel_core/defaults/` package entirely
- [src/marcel_core/skills/install_skills.py](../../src/marcel_core/skills/install_skills.py) — seeding CLI
- Any `seed_defaults()` call site in [main.py](../../src/marcel_core/main.py) or elsewhere

**marcel-zoo repo layout:**

```
marcel-zoo/
├── pyproject.toml          # deps for zoo habitats (EnableBanking, pyicloud, feedparser, ...)
├── README.md               # install instructions
├── integrations/
│   ├── banking/
│   ├── icloud/
│   ├── news/
│   ├── settings/
│   └── docker/
├── skills/
│   ├── banking/            # depends_on: [banking]
│   ├── icloud/             # depends_on: [icloud]
│   ├── news/               # depends_on: [news]
│   ├── settings/           # depends_on: [settings]
│   ├── docker/             # depends_on: [docker]
│   ├── web/                # pure-markdown
│   ├── memory/
│   ├── developer/
│   ├── jobs/
│   └── ui/
├── channels/
│   └── telegram/
├── jobs/
│   ├── sync/
│   ├── check/
│   └── scrape/
├── agents/
│   └── explore.md          # + others
├── MARCEL.md
├── routing.yaml
├── tests/                  # cross-habitat integration tests
├── .gitignore              # users/ is gitignored
└── users/                  # runtime, per-user working dirs — NOT tracked
```

**Install flow:**

Fresh Marcel install on a new machine:
1. `git clone marcel-core` → installs kernel.
2. Docker image / systemd service sees empty `~/.marcel/` on first boot, clones marcel-zoo there.
3. Zoo's `pyproject.toml` is `pip install`ed into Marcel's venv so integration deps (EnableBanking, pyicloud, etc.) are available.
4. Marcel discovers habitats from `~/.marcel/` and boots.
5. User edits `~/.marcel/MARCEL.md`, adds credentials via Marcel's onboarding flow — everything persistent.
6. `git pull` in `~/.marcel/` picks up new habitats (banking updates, new skills). User-created jobs and credentials live under `users/` which is gitignored.

**Docker implications:**
- `~/.marcel/` must be a mounted volume (already the case in [docker-compose.yml](../../docker-compose.yml)).
- [Dockerfile](../../Dockerfile) either (a) clones marcel-zoo during build or (b) leaves the clone to the first-boot entrypoint script. (b) is probably better — keeps the kernel image zoo-agnostic.
- Zoo deps install happens in the entrypoint after clone.

## Sessions

This issue is being shipped in chunks to keep each session's blast radius contained:

- **Session A (this branch):** delete the `defaults/` tree and its seed mechanism, move `MARCEL.md` + `routing.yaml` into marcel-zoo, move kernel channel-type prompts to `src/marcel_core/channel_prompts/`, update tests/docs/Makefile/Dockerfile/pyproject accordingly. Existing `~/.marcel/` checkouts keep working; fresh installs are handled by later sessions.
- **Session B (future):** author `marcel-zoo/pyproject.toml`, split habitat-only deps out of marcel-core's `pyproject.toml`.
- **Session C (future):** first-boot clone of marcel-zoo in Dockerfile/entrypoint, move cross-habitat integration tests to the zoo, zoo CI, SETUP.md / claude-code-setup.md updates, empty-zoo error handling.

## Tasks

- [⚒] Audit: verify [src/marcel_core/defaults/](../../src/marcel_core/defaults/) is empty of anything not already migrated. Anything remaining is a gap in the earlier issues — fix there, don't paper over here.
- [⚒] Delete `src/marcel_core/defaults/` package, `skills/install_skills.py`, and every call site of `seed_defaults()`.
- [⚒] Move `~/.marcel/MARCEL.md` and `~/.marcel/routing.yaml` into the zoo's root position (they're already in `~/.marcel/` at runtime per [defaults/__init__.py:83-89](../../src/marcel_core/defaults/__init__.py) — this step is about moving the source-of-truth into the zoo repo).
- [✓] Create `marcel-zoo` as a new git repo. Initial commit is the full `~/.marcel/` tree minus `users/`. `.gitignore` excludes `users/`, any `*.db`, `*.jsonl`, runtime state. *(done in prior sessions — habitats already migrated)*
- [ ] Author `marcel-zoo/pyproject.toml` — lists habitat Python deps (EnableBanking, pyicloud, feedparser, docker SDK, etc., whatever ISSUE-2ccc10 decided to externalize). **Deferred to Session B.**
- [ ] Update Marcel's install process: fresh-boot entrypoint clones marcel-zoo to `~/.marcel/` if empty, runs `pip install -e .` against the zoo's pyproject. **Deferred to Session C.**
- [ ] Update [Dockerfile](../../Dockerfile) / [docker-compose.yml](../../docker-compose.yml) for the new flow — mount, first-boot clone, dep install. **Deferred to Session C.**
- [ ] Update [SETUP.md](../../SETUP.md) and [docs/claude-code-setup.md](../../docs/claude-code-setup.md) with the marcel-core + marcel-zoo two-repo model. **Deferred to Session C.**
- [ ] Remove the marcel-core `pyproject.toml` deps that only existed for habitats (EnableBanking, pyicloud, feedparser, etc.). They move to zoo's pyproject. **Deferred to Session B.**
- [ ] Move cross-habitat integration tests from [tests/](../../tests/) to the zoo's `tests/` directory; kernel `tests/` keeps only plugin-API contract tests and core engine tests. **Deferred to Session C.**
- [ ] CI: marcel-zoo gets its own CI config — runs its tests against a known marcel-core version (pinned or latest-main). **Deferred to Session C.**
- [ ] Docs: `marcel-zoo/README.md` explains the habitat model for contributors. Marcel-core's `README.md` links to it as "where the personality lives". **Deferred to Session C.**
- [ ] Verify: starting Marcel with an empty `~/.marcel/` and no zoo installed produces a clear error asking the user to clone the zoo — not a stack trace. **Deferred to Session C.**

## Relationships

- Depends on: ISSUE-3c87dd, ISSUE-6ad5c7, ISSUE-2ccc10, ISSUE-bde0a1, ISSUE-e22176, ISSUE-7d6b3f, ISSUE-a7d69a (all preceding habitat migrations must ship first)
- Final step of the marcel-zoo extraction plan.

## Implementation Log

### 2026-04-21 — Session A (delete defaults/, migrate MARCEL.md + routing.yaml to zoo)

**Scope:** trim marcel-core to stop bundling `MARCEL.md`, `routing.yaml`, and the seed mechanism. The zoo repo (at `/home/shbunder/projects/marcel-zoo/`) now owns those two files. Sessions B (pyproject split) and C (first-boot clone, SETUP.md, empty-zoo verify, zoo CI) remain to be done.

**marcel-zoo repo (separate repo, separate commit):**
- Added `MARCEL.md` and `routing.yaml` at the zoo root, migrated verbatim from `src/marcel_core/defaults/`. Committed as `add MARCEL.md and routing.yaml (migrated from marcel-core defaults/)`.

**marcel-core — deletions:**
- `src/marcel_core/defaults/__init__.py` (the `seed_defaults()` function) — gone; the kernel no longer seeds anything on first boot.
- `src/marcel_core/defaults/MARCEL.md`, `routing.yaml`, and the entire package — gone; zoo is the source of truth.
- `src/marcel_core/skills/install_skills.py` — gone; it only existed to run `seed_defaults()` manually.
- `tests/core/test_defaults.py` — gone; tested behavior that no longer exists.

**marcel-core — renames:**
- `src/marcel_core/defaults/channels/*.md` → `src/marcel_core/channel_prompts/*.md`. These are kernel-owned prompts for kernel channel types (`app`, `cli`, `ios`, `job`, `websocket`), not habitat content, so they stay in marcel-core — just moved out of the defaults-seeding directory into a clearly kernel-owned location.

**marcel-core — code changes:**
- [src/marcel_core/harness/context.py](../../src/marcel_core/harness/context.py): `_DEFAULTS_CHANNELS` → `_CHANNEL_PROMPTS_DIR`, pointing at the new kernel-owned `channel_prompts/` location. `load_channel_prompt()` docstring updated.
- [src/marcel_core/harness/tier_classifier.py](../../src/marcel_core/harness/tier_classifier.py): deleted `_DEFAULTS_PATH` (which read `defaults/routing.yaml`), replaced with an in-code `_FALLBACK_CONFIG` — empty patterns + `default_tier=STANDARD`. A broken or missing `routing.yaml` still cannot brick the router, but the pattern-rich defaults now come exclusively from the zoo's `routing.yaml`. `_defaults_cache` and `_defaults()` removed.
- [src/marcel_core/main.py](../../src/marcel_core/main.py): removed the `seed_defaults()` import and call from `lifespan()`. Kernel boot now does discovery only, no seeding.

**marcel-core — operational / config:**
- [Makefile](../../Makefile): deleted the `install-skills` target (its only job was to invoke `seed_defaults()`) and removed it as a dependency of `serve`.
- [Dockerfile](../../Dockerfile): replaced the stale `seed_defaults` comment with a note that the host must mount a populated `~/.marcel/` (zoo-cloned) for Marcel to have personality. First-boot-clone logic is deferred to Session C.
- [pyproject.toml](../../pyproject.toml): removed `*/skills/install_skills.py` from the coverage omit list.
- [.gitignore](../../.gitignore): updated the comment to explain that the zoo supplies `MARCEL.md` and `routing.yaml`, not a kernel seeder.

**marcel-core — tests:**
- [tests/harness/test_tier_classifier.py](../../tests/harness/test_tier_classifier.py): rewrote the "defaults" tests to cover the new fallback semantics — `test_missing_file_uses_safe_fallback` and `test_broken_yaml_falls_back_to_safe_default` both assert the empty-pattern fallback with `STANDARD` default. Added a module-level `_SAMPLE_ROUTING_YAML` constant and updated the `cfg` fixtures in `TestClassifyInitialTier` and `TestFrustration` to write it before calling `load_routing_config()`, since the kernel no longer ships in-code patterns.
- [tests/core/test_main_lifespan.py](../../tests/core/test_main_lifespan.py): dropped the `marcel_core.defaults.seed_defaults` patch since the call site is gone. Test still guards the ordering of `discover()` vs `scheduler.start()`.

**marcel-core — docs:**
- [docs/web.md](../../docs/web.md): rewrote the ISSUE-072 migration note to describe the zoo `git pull` path rather than the deleted in-kernel seeder.
- [docs/routing.md](../../docs/routing.md): rewrote the "Where the file lives" section — the file now ships with the zoo; the kernel's broken-edit fallback is the in-code empty default; added a `git checkout routing.yaml` restore tip.

**marcel-core — developer rules:**
- [.claude/rules/docs-in-impl.md](../../.claude/rules/docs-in-impl.md) and [.claude/agents/pre-close-verifier.md](../../.claude/agents/pre-close-verifier.md): updated the straggler-grep scope — the old `src/marcel_core/defaults/` path no longer exists, so grep scope now covers `~/.marcel/` (the zoo checkout) plus `docs/`, `project/`, `.claude/`.

**Verification:**
- `make check` — 1332 tests passed, coverage 91.35% (gate: 90%), no lint/type/format issues.
- Existing `~/.marcel/` installs are unaffected: their `MARCEL.md` and `routing.yaml` are already on disk; the kernel just stops re-seeding them.
- Fresh installs without the zoo cloned will now boot with no `MARCEL.md` (generic personality) and the empty routing fallback (every session is STANDARD). A clear error / onboarding prompt is still owed — deferred to Session C.

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
