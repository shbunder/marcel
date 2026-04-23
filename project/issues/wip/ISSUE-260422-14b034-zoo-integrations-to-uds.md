# ISSUE-14b034: Migrate zoo integrations to UDS isolation (Phase 2 of f60b09)

**Status:** WIP
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

- [ ] Extend `scripts/zoo-setup.sh` with per-habitat `.venv` creation gated on `isolation: uds`
- [ ] Update `scripts/zoo-setup.sh --deps-only` to mirror the same per-habitat path for container-side installs
- [ ] Migrate `docker` habitat to UDS (zoo PR)
- [ ] Migrate `news` habitat to UDS (zoo PR)
- [ ] Migrate `banking` habitat to UDS (zoo PR)
- [ ] Migrate `icloud` habitat to UDS (zoo PR)
- [ ] Remove migrated habitats' deps from zoo root `pyproject.toml`
- [ ] Verify `make check` stays green across all migrations
- [ ] Document the migration pattern in `docs/plugins.md` ("how to migrate an inprocess habitat to UDS")
- [ ] `/finish-issue` → merged close commit on main

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
