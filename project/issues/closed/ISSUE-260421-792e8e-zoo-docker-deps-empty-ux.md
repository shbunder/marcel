# ISSUE-792e8e: Close kernel-side zoo extraction — container deps, empty-zoo UX, first-party slot cleanup

**Status:** Closed
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** infrastructure, docs, ux

## Capture
**Original request:** Session C.2 of ISSUE-63a946: close out the kernel-side marcel-zoo extraction work. Three related threads: (1) in-container zoo-deps install — the Docker image is currently baked with only kernel deps (caldav/vobject were removed from marcel-core's pyproject in Session B, closed 2e219dd), so iCloud integration fails ImportError silently at habitat discovery inside prod containers; ship a `make zoo-docker-deps` target that runs the zoo-setup dep install inside the running container via `docker exec`, and wire it into the `make setup` flow after `docker compose up`. (2) empty-zoo UX polish — today `channels.discover()` and `jobs.discover_templates()` silent no-op when MARCEL_ZOO_DIR is unset or the zoo is empty, so a fresh install with no `make zoo-setup` boots with zero habitats and the operator gets no feedback; add a startup INFO log line showing resolved MARCEL_ZOO_DIR and habitat counts (channels / skills / jobs / agents), plus a loud WARNING if the zoo is missing or empty. (3) docs/skills.md:100 and src/marcel_core/skills/integrations/__init__.py still describe a "first-party integrations slot" at src/marcel_core/skills/integrations/<name>.py that is empty post-Session-A (defaults/ removal on d32c1a3) — verifier surfaced this as out-of-scope cleanup during C.1's pre-close review; remove the framing or mark the slot's deliberate vestigiality explicit. Explicitly deferred: zoo GitHub Actions CI (belongs in the zoo repo as its own issue there), replacement of marcel-zoo/conftest.py sys.path shim (dev use case still depends on it, deferred again from Session C of the parent issue), and a git-push step in /finish-issue (meta-workflow, separate issue). Closes the kernel-side work on ISSUE-63a946; only zoo-repo-side follow-ups remain after C.2.

**Follow-up Q&A:** None — scope was surveyed and proposed by the assistant, user approved with "ok to proceed".

**Resolved intent:** This is the final kernel-side session for the marcel-zoo extraction (ISSUE-63a946). After Sessions A, B, and C.1 carved out the zoo repo, extracted its pyproject, and shipped host-side `make zoo-setup` + docs, three loose ends remain inside marcel-core: the prod Docker image still ships with only kernel deps (so habitats importing caldav/vobject fail silently at discovery); a fresh install with no zoo is invisible (no log, no warning); and two code sites still advertise a "first-party integrations slot" that was removed in Session A. This session closes all three and lets the parent issue ISSUE-63a946 hand off cleanly to zoo-repo follow-ups.

## Description

Three narrow, related threads. All three close kernel-side work started by ISSUE-63a946 — nothing new is introduced.

### Thread 1 — in-container zoo deps install

Session B removed zoo-specific dependencies (caldav, vobject) from `pyproject.toml`. The image built by `make docker-up` therefore no longer contains them. On the host, `make zoo-setup` (shipped in C.1) installs them into the kernel venv, but there is no equivalent path for the container.

- Add a `make zoo-docker-deps` target that runs `scripts/zoo-setup.sh` logic inside the running container via `docker exec` (reads `/host/.marcel/zoo/pyproject.toml` on the bind-mounted volume, then `uv pip install` into the container venv).
- Wire this into `make setup` so a first-boot operator who runs `make setup` ends up with a container that has the zoo deps.
- Mirror the `--sync` ergonomics: a `make zoo-docker-sync` (or equivalent) for re-install after zoo updates.

### Thread 2 — empty-zoo UX polish

`channels.discover()` (plugin/channels.py:190-215) and `jobs.discover_templates()` currently return silently when `MARCEL_ZOO_DIR` is unset or empty. A fresh install without `make zoo-setup` boots with zero habitats — no feedback to the operator.

- At kernel startup, log one INFO line showing resolved `MARCEL_ZOO_DIR` + habitat counts (channels / skills / jobs / agents).
- Emit a WARNING when the zoo is missing or empty (zero habitats total), pointing at `make zoo-setup`.

### Thread 3 — first-party integrations slot cleanup

Two files still describe a "first-party integrations slot at `src/marcel_core/skills/integrations/<name>.py`" that was emptied by Session A (defaults/ removal on d32c1a3):

- `docs/skills.md:100`
- `src/marcel_core/skills/integrations/__init__.py` (module docstring)

Remove the framing — the slot is either vestigial and deliberately so (and should be marked as such) or it should be removed entirely. Decide based on whether anything still imports from the package.

### Out of scope (explicitly deferred)

- **Zoo GitHub Actions CI** — belongs in the zoo repo as its own issue there.
- **Replace marcel-zoo/conftest.py sys.path shim** — dev workflow still depends on it; deferred again from the parent issue.
- **`/finish-issue` git-push step** — meta-workflow, separate issue.

## Tasks

- [✓] Add `make zoo-docker-deps` target that runs zoo dep install inside the running container via `docker exec`. Refactored `scripts/zoo-setup.sh` with a `--deps-only` flag so one script drives both host-side and container-side installs.
- [✓] Wire `zoo-docker-deps` into `make setup` (so first-boot gives a container with zoo deps). `setup.sh` now runs host-side zoo clone + deps install, then `docker exec marcel bash /app/scripts/zoo-setup.sh --deps-only` to refresh container deps.
- [✓] Add `make zoo-docker-sync`: chains `zoo-sync` (host git pull + deps refresh) → `zoo-docker-deps` (container deps refresh). One command after a zoo update.
- [✓] Add startup INFO log: resolved `MARCEL_ZOO_DIR` + habitat counts (channels / integrations / skills / jobs / agents). New `_log_zoo_summary()` helper in `main.py`, called from `lifespan()` after integration discovery.
- [✓] Add startup WARNING when zoo is missing or empty, pointing at `make zoo-setup` and `make zoo-docker-deps`. Three cases covered: unset env var, nonexistent path, existing-but-empty zoo.
- [✓] Decide on `src/marcel_core/skills/integrations/` — package kept as the plugin surface (decorator, metadata registry, discovery entry), but the empty "first-party integrations slot" is gone: deleted `_discover_builtin()` + its dead `pkgutil`/`importlib` machinery, collapsed `discover()` and `_discover_external()` into a single zoo-only `discover()`.
- [✓] Update `docs/skills.md:100` — removed the numbered "first-party / zoo habitat" list; integrations live in the zoo, period.
- [✓] Update `src/marcel_core/skills/integrations/__init__.py` docstring — dropped the "first-party" framing; describes zoo-only discovery.
- [✓] Run straggler grep for `first-party`, `integrations slot`, `src/marcel_core/skills/integrations/<name>`, `defaults/` across docs + .claude + src + tests. One active section-heading straggler found in `docs/plugins.md:256` ("First-party vs. external integrations") — renamed to "Where integrations live". Five remaining hits ("kernel ships zero first-party integrations") are correct statements of the post-extraction reality and were left alone. One hit in `src/marcel_core/config.py:57` ("only first-party habitats inside marcel_core" — comment describing MARCEL_ZOO_DIR-unset behavior) left alone: `config.py` is a restricted path and the phrasing is misleading-but-technically-correct; not worth an unlock for this session.
- [✓] `make check` green: 1334 pass, 91.33% coverage, 4 new tests in `test_main_lifespan.py` + 2 test files updated (`_discover_external` → `discover` rename).
- [✓] `/finish-issue` → merged close commit on main

## Relationships
- Follows: [[ISSUE-12e920-zoo-first-boot-setup-docs]] (Session C.1, closed 5a3536f)
- Follows: [[ISSUE-2e219dd-zoo-pyproject-extract]] (Session B, closed 2e219dd)
- Follows: [[ISSUE-d32c1a3-defaults-removal]] (Session A, closed d32c1a3)
- Parent: [[ISSUE-63a946-marcel-zoo-extraction]] — this session closes the kernel-side work; only zoo-repo follow-ups remain.

## Implementation Log

### 2026-04-21 — three-thread impl commit (ca934ec)

- **Thread 1 — container zoo deps**: `scripts/zoo-setup.sh` gains a `--deps-only` flag (skips git clone/pull, requires the zoo to already exist at `$MARCEL_ZOO_DIR` — used inside the prod container where the zoo is bind-mounted from host). Mutex with `--sync`. Makefile adds `zoo-docker-deps` (with a running-container precondition check) and `zoo-docker-sync` (chains `zoo-sync` → `zoo-docker-deps`). `scripts/setup.sh` now runs host-side `zoo-setup.sh` + `docker exec marcel bash /app/scripts/zoo-setup.sh --deps-only` after the health check.
- **Thread 2 — empty-zoo UX**: `_log_zoo_summary()` in `main.py` resolves `settings.zoo_dir`, counts on-disk habitats under `channels/integrations/skills/jobs/agents/`, emits one INFO line on success or a WARNING pointing at `make zoo-setup` + `make zoo-docker-deps` for the three failure modes (unset env, nonexistent path, empty zoo). Called from `lifespan()` after `discover_integrations()`. Four tests added in `tests/core/test_main_lifespan.py`.
- **Thread 3 — first-party integrations slot removal**: deleted `_discover_builtin()` and its dead `pkgutil` + `importlib` machinery from `src/marcel_core/skills/integrations/__init__.py`. Collapsed `discover()` and `_discover_external()` into a single zoo-only `discover()`. Module docstring rewritten to drop the "first-party" framing. `docs/skills.md` "Adding a Python integration" section rewritten. `docs/plugins.md` section heading "First-party vs. external integrations" renamed to "Where integrations live" to match body. Two obsolete tests deleted (`test_discover_skips_underscore_modules`, `test_discover_handles_import_error` — testing `pkgutil` behavior that no longer exists). `test_discover_does_not_raise` replaced with `test_discover_noop_when_zoo_unset`. Renamed `_discover_external` → `discover` in 24 callsites across `tests/core/test_plugin.py` and `tests/jobs/test_habitat_jobs.py` (sed-rename).

### 2026-04-21 — verifier-driven fixups (second 🔧 impl commit)

Pre-close-verifier returned REQUEST CHANGES. Two blockers and two deferrable-but-fix-now items addressed:

- **(Shortcut)** `tests/core/test_plugin.py:261` — method name `testdiscover_is_idempotent` (missing underscore after `test_`, sed-rename collateral). Pytest still picked it up via the `test*` pattern, but the naming was inconsistent. Fixed to `test_discover_is_idempotent`.
- **(Straggler)** `SETUP.md:60-66` — "When a new habitat lands upstream, run `make zoo-sync`" was incomplete for prod-container operators: post-this-issue `zoo-sync` only refreshes the host kernel venv. Updated to show both `make zoo-sync` (dev) and `make zoo-docker-sync` (prod), with a two-line explanation of why two targets exist.
- **(Deferrable — fixed anyway)** `src/marcel_core/config.py:55,57,180` — three comments that this issue made actively wrong: the `marcel_zoo_dir` field's docstring claimed the default was `~/projects/marcel-zoo` (actual: `~/.marcel/zoo`, pre-existing C.1 straggler) and said "only first-party habitats inside marcel_core are loaded" when unset (post-this-issue: zero are loaded). Same inaccuracy at the `zoo_dir` property's docstring. Rewrote both blocks. `config.py` is a restricted path; unlocked via `.claude/.unlock-safety`, made the edits, re-locked immediately.
- **(UX note — fixed anyway)** `scripts/setup.sh` post-install message: the container finished `discover_integrations()` at startup *before* the zoo deps got installed, so the first-boot container has broken imports for any zoo integration that depends on the newly-installed deps (caldav, vobject). Added a WARN block instructing the operator to run `make docker-restart` to pick them up. Not blocking, but the verifier correctly pointed out the silent gap.

**Reflection** (via pre-close-verifier):
- Initial verdict: REQUEST CHANGES (1 shortcut + 1 straggler + 2 deferrable stragglers)
- Follow-up: all four addressed in a second 🔧 impl commit before the close
- Coverage: 10/10 tasks addressed (task #11 is `/finish-issue` merge itself)
- Shortcuts found: 1 (typo) — fixed
- Scope drift: none
- Stragglers: 2 active (SETUP.md zoo-sync, config.py comments) — both fixed; 5 "kernel ships zero first-party integrations" references left alone as correct statements of post-extraction reality
- Restricted-path unlock: `config.py` touched via `.claude/.unlock-safety`, cleanup confirmed

## Lessons Learned

### What worked well

- **Single impl commit for the three threads**: they share the same "close out zoo extraction" narrative, and splitting them would have meant three pre-close-verifier runs instead of one. The verifier's per-commit feedback is high-signal; investing in one coherent commit + one verifier pass beats three thin commits + three passes.
- **Test seam for the startup log**: `_log_zoo_summary()` takes no arguments and reads `settings.zoo_dir` fresh, so `monkeypatch.setattr(main_module.settings, 'marcel_zoo_dir', ...)` is enough to exercise every branch. No need to plumb an optional path argument for testability.
- **Empirical straggler grep**: grepping for `first-party|integrations slot|skills/integrations/<name>` across `src/ docs/ .claude/ tests/` caught the `docs/plugins.md` section heading I would otherwise have missed. The verifier still found two more (SETUP.md zoo-sync, config.py comments) — the grep caught the string-level stragglers but the *semantic* stragglers (claims that became wrong after this change, without sharing the key term) need a different tool.

### What to do differently

- **Run the straggler grep against operator-facing docs separately**: I grepped for specific terms (`first-party`, etc.) but didn't think to grep for `make zoo-sync` to find operator instructions that became incomplete for a different reason (container deps). Next time, when a change splits an existing flow into two targets, grep for the old target name as its own straggler pass.
- **Sed-rename pitfalls**: `sed -i 's/_discover_external/discover/g'` swept over a method name that contained `_discover_external_`, producing `testdiscover_is_idempotent`. Running a post-sed `grep -E "def (test[^_]|test_$)"` or equivalent would have caught it before commit. Next time I do a mass-rename, verify test method names still match the `test_*` convention via a shape check, not just a "tests still pass" check (pytest's default pattern is lenient).
- **Restricted-path comments drift**: the `config.py:55,57,180` comments were wrong from the moment I deleted `_discover_builtin()`, but because `config.py` was restricted I excluded it from scope without re-reading whether my change made existing comments incorrect. The rule: when a change touches the semantics that a nearby restricted file's comments describe, grep that file even though you can't edit it — and decide whether the unlock is worth it. Usually yes.

### Patterns to reuse

- **On-disk habitat count as startup signal**: counting directories under `<zoo>/{channels,integrations,skills,jobs,agents}/` is cheap, independent of discovery ordering, and tells the operator what's *available* rather than what successfully *loaded*. The two are subtly different (a broken habitat still counts on disk) and the on-disk count is the right signal for "did my `make zoo-setup` work?" questions. Pattern reusable for any discovery system that wants a boot-time sanity signal separate from registration.
- **`--deps-only` test seam for bash setup scripts**: when a script has two phases (fetch + apply), a flag that skips the first phase makes the second testable in isolation and also gives a clean handle for docker-exec'ing part of the flow. Same shape as the `DRY_RUN=1` seam in `redeploy.sh` from ISSUE-5ca6dc — two recent issues, same pattern.
- **Chained Makefile targets for composite ergonomics**: `zoo-docker-sync: zoo-sync zoo-docker-deps` is cleaner than a shell script that calls both. Operator muscle memory picks the verb; Make picks the order; each target remains individually invocable.
