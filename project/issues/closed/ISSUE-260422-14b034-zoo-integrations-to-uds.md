# ISSUE-14b034: Migrate zoo integrations to UDS isolation (Phase 2 of f60b09)

**Status:** Closed
**Created:** 2026-04-22
**Assignee:** Claude
**Priority:** Medium
**Labels:** refactor, plugin-system, isolation, marcel-zoo

## Capture

**Follow-up to [[ISSUE-f60b09]] (Phase 1)** — Phase 1 landed the kernel-side UDS mechanism + a fixture habitat proving the shape end-to-end. Real zoo integrations (`docker`, `icloud`, `news`, `banking`) still run in-process. This issue migrates them one by one, delivers the dep-isolation win, and updates `make zoo-setup` to create per-habitat venvs.

**Resolved intent:** For each existing zoo integration habitat, add `isolation: uds` to `integration.yaml`, lift declared deps into a per-habitat `pyproject.toml`, and wire `make zoo-setup` to provision a `.venv` per habitat. The kernel venv stops carrying zoo deps — the iCloud caldav version and the news feedparser version can now differ from whatever the banking integration pins. Migration is per-habitat; one habitat's migration PR is independent of the others.

## Description

### Migration shape (per habitat)

For `<MARCEL_ZOO_DIR>/integrations/<name>/`:

1. Add `isolation: uds` to `integration.yaml`.
2. Create `<name>/pyproject.toml` declaring `[project].dependencies` that today live in the zoo's root `pyproject.toml` under that habitat's name.
3. Remove the same deps from the root `<MARCEL_ZOO_DIR>/pyproject.toml` (or leave a compatibility shim if habitats co-install in a single venv during migration — prefer removal for one-habitat-at-a-time clarity).
4. Verify the habitat's `__init__.py` imports are all satisfied by its declared deps — no sneaky dep on something the kernel also happens to install.
5. Test: run Marcel with `make serve`, verify the habitat's subprocess spawns (`journalctl --user -u marcel | grep uds-supervisor`), verify an integration call round-trips.

### `make zoo-setup` update

`scripts/zoo-setup.sh` walks `<zoo>/integrations/*/`. For each habitat with `isolation: uds` declared:

- Check for `<habitat>/pyproject.toml`.
- `uv venv <habitat>/.venv --python 3.12` if missing.
- `uv pip install --python <habitat>/.venv/bin/python -r <habitat>/pyproject.toml`.

`--deps-only` mode (container-side, from ISSUE-792e8e) does the same inside the container. The flat root-`pyproject.toml` install path remains as a fallback for inprocess habitats during migration.

### Rollout order

Cheapest first so early misses stay contained:

1. `docker` — thinnest deps (docker-py), smallest surface.
2. `news` — pure HTTP + feedparser; easy to smoke-test (fetch a feed, assert non-empty output).
3. `banking` — credential-heavy; good test of the "credentials in RPC params" flow.
4. `icloud` — heaviest (caldav, vobject, login state); biggest isolation win, most careful to migrate.

Each migration is a separate `🔧 impl:` commit. If one blows up, the others stay on main.

## Tasks

- [✓] Extend `scripts/zoo-setup.sh` with per-habitat `.venv` creation gated on `isolation: uds`
- [✓] Update `scripts/zoo-setup.sh --deps-only` to mirror the same per-habitat path for container-side installs
- [✓] Migrate `docker` habitat to UDS (zoo PR)
- [✓] Migrate `news` habitat to UDS (zoo PR)
- [✓] Migrate `banking` habitat to UDS (zoo PR)
- [✓] Migrate `icloud` habitat to UDS (zoo PR)
- [✓] Remove migrated habitats' deps from zoo root `pyproject.toml`
- [✓] Verify `make check` stays green across all migrations
- [✓] Document the migration pattern in `docs/plugins.md` ("how to migrate an inprocess habitat to UDS")
- [✓] `/finish-issue` → merged close commit on main

## Non-scope

- Channel / job habitat UDS support → [[ISSUE-931b3f]] (Phase 3)
- Removing the inprocess path entirely → [[ISSUE-807a26]] (Phase 4)
- Connection pooling in the UDS proxy (evaluate after Phase 3 with real latency data)

## Relationships

- Follows: [[ISSUE-f60b09]] (Phase 1 — kernel mechanism)
- Precedes: [[ISSUE-931b3f]] (Phase 3 — channels/jobs)
- Precedes: [[ISSUE-807a26]] (Phase 4 — remove inprocess)

## Implementation Approach

**Key finding up front:** a dep-survey across the four habitats
(`grep '^import\|^from' /home/shbunder/projects/marcel-zoo/toolkit/*/*.py`)
shows that **only `icloud` has non-kernel deps** — `caldav` (plus its
transitive `vobject`). The other three are stdlib + kernel-transitive:

| Habitat | Non-stdlib imports | Already kernel deps? |
|---|---|---|
| `docker` | *(none — uses subprocess `docker` CLI)* | — |
| `news` | `yaml` | yes (kernel) |
| `banking` | `httpx`, `jwt` | yes (kernel) |
| `icloud` | `caldav`, `vobject` (transitive) | **no** |

That means the UDS migration is **dep-isolation-valuable for icloud
only**. For the other three, UDS buys pure failure-isolation (a
segfaulting handler doesn't take the kernel down) and
concurrency-per-habitat. Both still worth having, but the `pyproject.toml`
for those habitats declares an empty dependency list.

### Work order (this session)

**1. Kernel-side (this repo):**
- Extend `scripts/zoo-setup.sh` with a `_provision_uds_habitats()` helper
  that walks `<zoo>/toolkit/*/` and, for each habitat with
  `isolation: uds` in `toolkit.yaml`, creates `<habitat>/.venv` (via
  `uv venv --python 3.12`) and installs the habitat's
  `pyproject.toml` deps into it. No-op for `inprocess` habitats. Runs
  after the existing kernel-venv install.
- Same logic under `--deps-only` mode (for container-side provisioning
  from `make zoo-docker-deps`).
- Extend `docs/plugins.md` "Isolation modes" section with a
  "Migrating from inprocess to UDS" recipe.

**2. Zoo-side (cross-repo, separate commits in the zoo):**
- `toolkit/icloud/pyproject.toml` — new, declares `caldav>=3.1.0` +
  `vobject>=0.9.9`. Remove both from zoo root `pyproject.toml`.
- `toolkit/icloud/toolkit.yaml` — add `isolation: uds`.
- `toolkit/docker/`, `toolkit/news/`, `toolkit/banking/` — each gets a
  minimal `pyproject.toml` declaring no deps, plus `isolation: uds` in
  its `toolkit.yaml`. The per-habitat venv in this case exists for the
  subprocess-shell (it still needs `marcel_core` importable from
  inside; the bridge handles that via `PYTHONPATH` — verify by
  reading `src/marcel_core/plugin/_uds_bridge.py`).

**3. Verification:**
- `make check` on kernel stays green (docs + script changes only).
- `./scripts/zoo-setup.sh --sync` creates four per-habitat `.venv`
  dirs under `toolkit/*/`.
- Kernel import smoke: `python -c "from marcel_core.toolkit import discover; from marcel_core.config import settings; discover()"` logs
  `uds-supervisor: spawned habitat 'icloud'` (and the other three) if
  env is configured, or a clean "skipped — no pyproject" message
  otherwise. Live integration-call verification requires the dev
  container + credentials; document the manual verification steps in
  the close note rather than block on them here.

### Files touched

Kernel:
- `scripts/zoo-setup.sh` — per-habitat venv provisioning
- `docs/plugins.md` — migration recipe

Zoo (separate commits, cross-repo):
- `toolkit/{docker,news,banking,icloud}/toolkit.yaml` — add `isolation: uds`
- `toolkit/{docker,news,banking,icloud}/pyproject.toml` — new per-habitat manifests
- `pyproject.toml` — drop `caldav` + `vobject` (moved to icloud habitat)

### Risk + rollback

Low blast radius: a habitat that fails to spawn is logged by the
supervisor and leaves the registry handler-less (the kernel continues
to boot). Rollback is a one-line `toolkit.yaml` edit — remove
`isolation: uds` — no kernel redeploy required.

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 19:21 - LLM Implementation
**Action**: Extended scripts/zoo-setup.sh with per-habitat .venv provisioning gated on isolation: uds; walks toolkit/ and legacy integrations/ layouts; installs marcel-core (editable, from $REPO_ROOT) plus each habitat's declared deps. Documented migration recipe in docs/plugins.md. Zoo side: migrated all four toolkit habitats (icloud dep-isolation, docker/news/banking failure-isolation) in four separate commits on issue/14b034-icloud-to-uds; merged to zoo main at SHA 611a676. End-to-end verified: zoo-setup provisions 4 venvs, kernel discovery spawns 4 bridge subprocesses, registers 16 UDS-proxy handlers, supervisor shuts down cleanly.
**Files Modified**:
- `scripts/zoo-setup.sh`
- `docs/plugins.md`
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
- **Dep-survey before the migration.** The `grep '^import\|^from' toolkit/*/*.py` step up front exposed that only icloud had non-kernel deps. This reframed the plan honestly — three of the four migrations are *pure* failure-isolation, not dep-isolation. Writing that into the issue's Implementation Approach up front meant the per-habitat pyproject.toml files for docker/news/banking could declare `dependencies = []` with a comment explaining why, instead of feeling like they were half-finished.
- **Reaching for `uv pip install -e "$REPO_ROOT"` instead of PYTHONPATH.** The kernel's `habitat_python()` helper picks `<habitat>/.venv/bin/python` when present — but that venv needs `marcel_core` importable from inside to run `python -m marcel_core.plugin._uds_bridge`. Installing marcel-core editable from the kernel checkout is simpler than plumbing PYTHONPATH through `_bridge_command` / `_spawn`, and it makes each habitat venv self-describing. uv's cache means the duplicated install is disk-cheap.
- **End-to-end spawn-and-shutdown test before committing.** Running `discover(); stop_supervisor()` against the migrated zoo surfaced that all four bridges spawn cleanly and shut down at teardown — concrete evidence the mechanism works beyond just "the script ran without erroring". The approach of separate zoo commits per habitat ([x]-to-UDS) plus the shared kernel-side commit kept audit clean.
- **Pre-close-verifier as a straggler-hunter.** The verifier flagged `scripts/setup.sh:170-194` as stale (still describing a pre-UDS world where caldav lived in the kernel venv). That's exactly the kind of comment that doesn't break anything but confuses the next operator reading setup.sh — cheap to fix now, expensive to rediscover later.

### What to do differently
- **CWD drift across repos.** Three times I hit `PreToolUse:Edit hook error: can't open file '/home/shbunder/projects/marcel-zoo/.claude/hooks/guard-restricted.py'` because I'd `cd`-ed into the zoo for a bash command. The hook resolves its own path relative to the shell's cwd, so editing any file while in a non-kernel cwd breaks the hook. Fix: always `cd /home/shbunder/projects/marcel` after any `cd` into a sibling repo, even if the next edit target is outside the kernel repo. The Edit tool uses absolute paths — the hook doesn't.
- **zoo-setup.sh early-exit.** The original script did `exit 0` when the root `pyproject.toml` had no deps. Fine before, broken post-migration when empty-root-deps is the new normal. When extending an idempotent setup script, always trace the no-op branches — the early exit skipped the UDS provisioning on the first run.

### Patterns to reuse
- **Empty-deps `pyproject.toml` as a first-class habitat artefact.** For failure-isolation-only migrations (handlers that use stdlib + kernel-transitive deps), declaring `dependencies = []` with a comment explaining "UDS isolation here is pure failure-isolation" is the right shape. The `.venv` still gets created, marcel-core installs into it, and the habitat subprocess runs in true isolation even though it technically could live in the kernel venv.
- **"Editable kernel install" as the marcel-core propagation mechanism.** `uv pip install --python <habitat_venv>/bin/python -e "$REPO_ROOT"` is the one-liner that makes bridge subprocesses self-sufficient. Pattern: for any subprocess that needs to `python -m marcel_core.<submodule>`, install marcel-core editable into the subprocess's venv. Don't reach for PYTHONPATH unless the subprocess env isn't reachable at setup time.
- **Cross-repo close summary including zoo SHA.** Recording the zoo merge SHA (`611a676`) in the kernel's close commit + Implementation Log pins the two repos' state together, making a future bisect easier.
