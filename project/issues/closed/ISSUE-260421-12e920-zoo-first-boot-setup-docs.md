# ISSUE-12e920: First-boot marcel-zoo setup (Makefile + SETUP docs + two-repo model)

**Status:** Closed
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** infrastructure, docs

## Capture

**Original request:** Session C.1 of ISSUE-63a946: first-boot marcel-zoo clone + SETUP.md / docs/claude-code-setup.md / README.md updates for the two-repo model. Session A (closed d32c1a3) deleted defaults/ and moved MARCEL.md + routing.yaml into the zoo. Session B (closed 2e219dd) authored marcel-zoo/pyproject.toml and removed the habitat-only [project.optional-dependencies].zoo extra from marcel-core. Session C is being split into C.1 (this issue — kernel infra + docs) and C.2 (zoo CI + empty-zoo verification). Scope of C.1: (a) add a first-boot entrypoint that `git clone`s github.com/shbunder/marcel-zoo into ~/.marcel/ (or $MARCEL_ZOO_DIR) when it's empty and runs `uv sync` against the zoo pyproject so caldav/vobject are installed; (b) wire it into Dockerfile + docker-compose.yml + docker-compose.dev.yml so fresh prod + dev containers bootstrap themselves; (c) update SETUP.md with an explicit "clone the zoo" prerequisite section — today it has no mention of cloning marcel-zoo; (d) update docs/claude-code-setup.md and marcel-core README.md with the two-repo model; (e) non-task: habitat tests already live in zoo (integrations/{icloud,banking,news}/tests, channels/telegram/tests), no kernel test imports a habitat — verify no stragglers but no migration needed. Does NOT include: zoo GitHub Actions CI (Session C.2), empty-zoo error-message polish (Session C.2), replacing the zoo's conftest sys.path shim (deferred — dev use case still depends on it).

**Follow-up Q&A:**
- Q: container-side entrypoint clone vs. host-side `make` target? A: host-side `make zoo-setup` + SETUP.md step. See the "Clone strategy" decision in the Description below.

**Resolved intent:** With `defaults/` gone (Session A) and habitat-only deps factored into the zoo's own pyproject (Session B), marcel-core no longer has a working "fresh install" story: `git clone marcel && make serve` starts a kernel with zero skills, zero `MARCEL.md`, zero `routing.yaml`, because everything user-facing now lives in the zoo repo. This issue closes that gap by giving operators a one-liner (`make zoo-setup`) that clones `github.com/shbunder/marcel-zoo` into `$MARCEL_ZOO_DIR` (default `~/.marcel/zoo`) if empty, and documenting the two-repo setup in SETUP.md / docs/claude-code-setup.md / README.md so a new operator knows the zoo is part of the install path. Session C.2 will layer CI and empty-zoo UX on top.

## Description

### Clone strategy: host-side Makefile, not container entrypoint

The parent issue phrased this as "fresh-boot entrypoint clones marcel-zoo". After reviewing `docker-compose.yml` and `docker-compose.dev.yml`, a **host-side `make zoo-setup` target** is the better fit:

- Both compose files mount `${HOME}/.marcel:${HOME}/.marcel` from host → container. The zoo directory the kernel reads is the *host's* directory — a clone from inside the container writes to the host anyway, so in-container-clone buys nothing over host-side-clone.
- Recoverable-over-fast (Marcel's core principle): an entrypoint that fails a `git clone` on first boot puts the container in a restart loop. A host-side `make zoo-setup` surfaces the error at operator time, in the operator's terminal, where it can be diagnosed.
- Consistency: users already clone `marcel` from the host as step 1 of SETUP.md (`git clone https://github.com/shbunder/marcel.git ~/projects/marcel`). `make zoo-setup` extends the same pattern rather than inventing a new one (container self-bootstrap) that exists nowhere else in Marcel.
- No new runtime network dependency. Containers boot offline as long as the zoo is already cloned — important for home-server scenarios where the network may be flaky.

If we ever need container self-bootstrap (e.g. deploying Marcel to a fresh cloud VM), a small entrypoint script can be added later. It's a pure addition, not a redesign.

### What goes in `make zoo-setup`

The target lives in the Marcel kernel's top-level `Makefile` and does exactly two things, idempotently:

1. `git clone https://github.com/shbunder/marcel-zoo.git "$MARCEL_ZOO_DIR"` if `$MARCEL_ZOO_DIR` does not exist or is empty. Defaults to `${HOME}/.marcel/zoo` to match `docker-compose.yml`.
2. Pulls the habitat deps (`caldav`, `vobject`) into the kernel venv so zoo imports resolve. The zoo's pyproject lists them under `[project] dependencies`, but the zoo is not installed as a package, so the kernel venv needs them directly. The simplest mechanism: the target reads `$MARCEL_ZOO_DIR/pyproject.toml` with `tomllib`, extracts `[project].dependencies`, and runs `uv pip install <deps>` against the kernel venv. That way, future habitats can add a dep to the zoo pyproject and `make zoo-setup` picks it up — no Makefile edit needed.

### Docker flow after this change

Nothing changes in the Dockerfile itself — it continues to build the kernel image, and the zoo is mounted from the host at runtime via `-v ${HOME}/.marcel:${HOME}/.marcel`. What changes is the **documented install sequence**: operators now run `make zoo-setup` once (host-side) before `make docker-up` / `make serve`. This is a SETUP.md edit, not a Dockerfile edit.

However — the kernel venv inside the image is baked at build time and does NOT include caldav/vobject (Session B removed them from marcel-core's pyproject). For production containers, the `uv pip install <zoo-deps>` step from `make zoo-setup` must also run inside the container, once, on first boot. Options:

- **Option A (chosen):** add the same `uv pip install` step to `make zoo-setup`, and document that the `docker-compose up` flow needs `make zoo-setup` + a one-time `docker exec marcel uv pip install caldav vobject` (or re-run the setup target inside the container). Ugly but explicit.
- **Option B (deferred to C.2):** bake a first-boot container-side hook into the watchdog that does `uv pip install <zoo-deps>` on startup if they're missing. Moves runtime network dep into the container — wait on empty-zoo UX decisions first.

### Non-task: habitat test migration

Verified pre-issue via `grep -rln "^(import|from) marcel_core\.channels\.telegram|from\s+\.\.integrations\.(icloud|banking|news)"` across `tests/` — **zero** kernel test files import a habitat module. Habitat-specific tests already live in the zoo (`integrations/{icloud,banking,news}/tests/`, `channels/telegram/tests/`), migrated during the per-habitat issues (e7d127, 13c7f2, d5f8ab, 7d6b3f). One task in this issue is a straggler grep to confirm no new references have crept in.

## Tasks

- [✓] Add `zoo-setup` target to [Makefile](../../Makefile) that (1) clones `github.com/shbunder/marcel-zoo` into `$MARCEL_ZOO_DIR` (default `${HOME}/.marcel/zoo`) if empty, (2) reads the zoo's `[project] dependencies` via `python -c 'import tomllib; ...'` and runs `uv pip install <deps>` against the kernel venv. Idempotent — re-running should be a fast no-op.
- [✓] Add a short `zoo-sync` target (or fold into `zoo-setup`): `git -C $MARCEL_ZOO_DIR pull --ff-only && <re-run deps install>`. Used by operators after a `git pull` of marcel-core to catch zoo updates too.
- [✓] Update [SETUP.md](../../SETUP.md): add a new section (probably section 2, after "Install Marcel") titled "Install the zoo" that says: `export MARCEL_ZOO_DIR=${HOME}/.marcel/zoo` in `.env.local`, then `make zoo-setup`. Explain that the kernel ships zero habitats — without the zoo, Marcel has no skills, no `MARCEL.md`, no `routing.yaml`.
- [✓] Update [docs/claude-code-setup.md](../../docs/claude-code-setup.md): replace any single-repo framing with a "marcel-core + marcel-zoo" section. Link to the zoo's own README for the habitat-author contract.
- [✓] Update [README.md](../../README.md) intro or architecture section: mention that the kernel ships zero first-party skills; habitats live in `marcel-zoo`. Link to the zoo repo. Adjust the one existing reference at line 105 to sit alongside this new framing instead of being the only mention.
- [✓] Straggler verification: grep `tests/` for `^(import|from) marcel_core\.channels\.telegram` and `from\s+\.\.integrations\.(icloud|banking|news)` — confirm zero hits (expected). Grep docs for stale references to `src/marcel_core/defaults/` left over from pre-Session-A.
- [✓] Verify `make zoo-setup` on a cold sandbox: `mv ~/.marcel/zoo /tmp/zoo-backup && make zoo-setup && ls ~/.marcel/zoo/MARCEL.md` — confirm clone succeeded. Restore.
- [✓] Run `make check` (90% coverage gate).
- [✓] Close via `/finish-issue` and merge to main.

## Relationships

- Follows: [[ISSUE-63a946-extract-marcel-zoo-repo]] (parent), [[ISSUE-0baea6-zoo-pyproject-dep-split]] (Session B — closed as `2e219dd`)
- Precedes: Session C.2 (separate issue — zoo CI + empty-zoo verification + README polish)

## Comments

### 2026-04-21 - Planner
Scope resolved into host-side Makefile rather than container entrypoint. Tradeoff explicit in the "Clone strategy" decision above: simpler, matches existing SETUP.md pattern, no runtime network dep on first boot. Container-side self-bootstrap can be added later if needed — it's a pure addition, not a redesign.

The docker-compose edits originally scoped for this session turned out to be a no-op: both `docker-compose.yml` and `docker-compose.dev.yml` already mount `${HOME}/.marcel:${HOME}/.marcel` and resolve `MARCEL_ZOO_DIR` to `${HOME}/.marcel/zoo` by default. No change needed.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-21 — Implementation

**Commits on `issue/12e920-zoo-first-boot-setup-docs`:**
- `895b0a0` — `scripts/zoo-setup.sh` + Makefile `zoo-setup` / `zoo-sync` targets. Script reads the zoo's `[project].dependencies` via `tomllib` and shells out to `uv pip install` against the kernel venv. Idempotent; fast no-op when the zoo is present and deps are installed.
- `c47ae92` — SETUP.md (new Step 2 "Install the zoo", top-of-page two-repo explainer, Steps 3–7 renumbered, Telegram anchor updated). README.md (quickstart now calls `make zoo-setup` between `setup-check` and `setup`; new "Kernel and zoo in separate repos" architectural bullet; Phase-3 marcel-zoo reference contextualised). docs/claude-code-setup.md (admonition clarifying the `.claude/` setup is kernel-scoped).
- `fdbcd91` — Straggler fix: `project/issues/CLAUDE.md:43` closing-verification checklist pointed at the deleted `src/marcel_core/defaults/` folder (removed by Session A). Repointed at `$MARCEL_ZOO_DIR`. Edited via the unlock-safety flag (CLAUDE.md is a restricted path), unlock was re-removed immediately after commit.

**Clone-strategy decision.** Reviewed in the issue Comments: host-side `make zoo-setup` chosen over a container entrypoint because both compose files already mount `${HOME}/.marcel:${HOME}/.marcel`, a clone failure inside a container entrypoint would create a restart loop, and the host-side pattern matches the existing `git clone marcel` step in SETUP.md. Container self-bootstrap stays a pure addition for future work (Session C.2 will decide).

**Docker-compose edits turned out to be a no-op.** Both `docker-compose.yml` and `docker-compose.dev.yml` already default `MARCEL_ZOO_DIR=${HOME}/.marcel/zoo` and mount `${HOME}/.marcel:${HOME}/.marcel` — nothing to change.

**In-container zoo-deps install.** The parent issue originally asked C.1 to wire `uv sync` against the zoo pyproject into the Dockerfile. Deferred to Session C.2 along with empty-zoo UX polish; the kernel image ships without caldav/vobject baked in, so the prod `docker-compose up` flow still needs a one-time `docker exec` for the container's deps. Explicit "open question" in the issue's Description.

**Cold-sandbox smoke test uncovered an unpushed-zoo blocker.** The first cold-clone failed — the remote `shbunder/marcel-zoo` was two commits behind local: `cc9da47` (Session B's `pyproject.toml`) and `9bfd1c2` (Session A's `MARCEL.md`/`routing.yaml`). Without those on GitHub, `make zoo-setup` would clone an incomplete zoo on any other machine. User pushed the two commits to `origin/main`; re-ran the cold test against a fresh temp `MARCEL_ZOO_DIR` override, zoo cloned cleanly, `MARCEL.md` / `routing.yaml` / `pyproject.toml` all present, deps install no-opped because already in venv.

**Coverage.** `make check` ran via the pre-commit hook on every impl commit — 1332 tests pass, coverage 91.35% (above the 90% gate).

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 9/9 tasks addressed
- Shortcuts found: none
- Scope drift: none (Dockerfile / compose / in-container deps hook / zoo CI / empty-zoo UX all explicitly deferred to Session C.2)
- Stragglers: none within scope. Verifier surfaced one out-of-scope observation: `docs/skills.md:100` and `src/marcel_core/skills/integrations/__init__.py` docstring still frame `src/marcel_core/skills/integrations/<name>.py` as a "first-party integrations slot" — that slot is empty post-Session-A. Logged below as C.2 candidate.

## Lessons Learned

### What worked well

- **Smoke-test via `MARCEL_ZOO_DIR` env override** instead of `mv ~/.marcel/zoo /tmp/zoo-backup`. The task wording suggested the `mv`-and-restore dance, but pointing the script at a fresh `/tmp/zoo-coldtest-$$` directory exercises the same clone-when-empty branch without risking a missing `~/.marcel/zoo` for the live watchdog or any other consumer mid-test. Safer and cleaner — pattern to reuse for any idempotent install-script verification.
- **Cold-sandbox smoke test caught a real blocker.** The first cold clone failed because `shbunder/marcel-zoo` was two commits behind local — Sessions A and B had committed locally but not pushed. Without the smoke test this would have been a silent shipped-but-inert feature, caught only by the first operator who ran `make zoo-setup` from a fresh machine. The "run the thing end-to-end against a real remote" check is worth its weight every time.
- **Host-side `make zoo-setup` over container entrypoint** — the compose files already mount `${HOME}/.marcel` host→container, so an in-container clone would've been the same files on the same host anyway, traded for a restart-loop failure mode. Host-side surfaces errors in the operator's terminal where they can be diagnosed.

### What to do differently

- **Push feature-branch dependencies before the first session that depends on them.** Session A and Session B each closed "on main" (merge commits landed) but nobody pushed the zoo repo until C.1's cold test failed. Add a `git push` step to the kernel-side merge that also pushes the paired zoo changes if any exist, OR open the zoo repo as a paired checkout and close-and-push both repos in one flow.
- **CLAUDE.md unlock flow is fine for single-line stragglers, but only if scoped.** The unlock→edit→commit→re-lock sequence worked cleanly for the one-line stale-path correction in `project/issues/CLAUDE.md:43`. If the fix had been multi-file, the unlock window widens and that's where the rule would start to protect real surface area. Keep unlock windows as short as possible.

### Patterns to reuse

- **Read pyproject.toml via `tomllib` + heredoc in a bash script** (`scripts/zoo-setup.sh`). Passes the path as an env var into a quoted `<<'PY'` heredoc so bash doesn't try to expand `$variables` in the Python body. Clean, no Python deps needed since tomllib is stdlib on 3.11+.
- **Idempotent install scripts using `[[ -d ]] && [[ -z "$(ls -A ...)" ]]`** to detect "missing or empty" in one expression. Keeps the clone branch cheap to repeat and gives operators a fast no-op on re-run.
- **Env-override smoke testing** — `MARCEL_ZOO_DIR=/tmp/... ./scripts/zoo-setup.sh` over destructive `mv`-and-restore. Any future idempotent-install-script verification should prefer the env-override pattern.

### For Session C.2

- Zoo GitHub Actions CI (unit tests in the zoo repo against a marcel-core checkout).
- Empty-zoo error-message polish — what happens when the operator skips `make zoo-setup` and boots the kernel anyway.
- In-container zoo-deps install mechanism — the parent issue's original "uv sync inside the container" scope. Currently the `docker-compose up` flow needs a one-time `docker exec marcel uv pip install caldav vobject` because the image is baked without them. Decide between watchdog first-boot hook vs. Dockerfile-time install via the zoo's pyproject.
- Replace `marcel-zoo/conftest.py` `sys.path` shim with an editable install against marcel-core (if we decide to take on packaging the zoo).
- **Carry-over from verifier**: `docs/skills.md:100` and `src/marcel_core/skills/integrations/__init__.py` still describe a first-party integrations slot that is empty post-Session-A. Either remove the framing or make the slot's deliberate vestigiality explicit.
- **Add a git-push step** to `/finish-issue` (or a paired flow) so zoo-side changes aren't left unpushed when kernel-side sessions close.
