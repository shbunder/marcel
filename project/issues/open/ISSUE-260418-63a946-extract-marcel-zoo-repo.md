# ISSUE-63a946: Delete defaults/ + extract marcel-zoo as a separate repo

**Status:** Open
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

## Tasks

- [ ] Audit: verify [src/marcel_core/defaults/](../../src/marcel_core/defaults/) is empty of anything not already migrated. Anything remaining is a gap in the earlier issues — fix there, don't paper over here.
- [ ] Delete `src/marcel_core/defaults/` package, `skills/install_skills.py`, and every call site of `seed_defaults()`.
- [ ] Move `~/.marcel/MARCEL.md` and `~/.marcel/routing.yaml` into the zoo's root position (they're already in `~/.marcel/` at runtime per [defaults/__init__.py:83-89](../../src/marcel_core/defaults/__init__.py) — this step is about moving the source-of-truth into the zoo repo).
- [ ] Create `marcel-zoo` as a new git repo. Initial commit is the full `~/.marcel/` tree minus `users/`. `.gitignore` excludes `users/`, any `*.db`, `*.jsonl`, runtime state.
- [ ] Author `marcel-zoo/pyproject.toml` — lists habitat Python deps (EnableBanking, pyicloud, feedparser, docker SDK, etc., whatever ISSUE-2ccc10 decided to externalize).
- [ ] Update Marcel's install process: fresh-boot entrypoint clones marcel-zoo to `~/.marcel/` if empty, runs `pip install -e .` against the zoo's pyproject.
- [ ] Update [Dockerfile](../../Dockerfile) / [docker-compose.yml](../../docker-compose.yml) for the new flow — mount, first-boot clone, dep install.
- [ ] Update [SETUP.md](../../SETUP.md) and [docs/claude-code-setup.md](../../docs/claude-code-setup.md) with the marcel-core + marcel-zoo two-repo model.
- [ ] Remove the marcel-core `pyproject.toml` deps that only existed for habitats (EnableBanking, pyicloud, feedparser, etc.). They move to zoo's pyproject.
- [ ] Move cross-habitat integration tests from [tests/](../../tests/) to the zoo's `tests/` directory; kernel `tests/` keeps only plugin-API contract tests and core engine tests.
- [ ] CI: marcel-zoo gets its own CI config — runs its tests against a known marcel-core version (pinned or latest-main).
- [ ] Docs: `marcel-zoo/README.md` explains the habitat model for contributors. Marcel-core's `README.md` links to it as "where the personality lives".
- [ ] Verify: starting Marcel with an empty `~/.marcel/` and no zoo installed produces a clear error asking the user to clone the zoo — not a stack trace.

## Relationships

- Depends on: ISSUE-3c87dd, ISSUE-6ad5c7, ISSUE-2ccc10, ISSUE-bde0a1, ISSUE-e22176, ISSUE-7d6b3f, ISSUE-a7d69a (all preceding habitat migrations must ship first)
- Final step of the marcel-zoo extraction plan.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
