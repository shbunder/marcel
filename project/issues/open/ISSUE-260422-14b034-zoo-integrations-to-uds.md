# ISSUE-14b034: Migrate zoo integrations to UDS isolation (Phase 2 of f60b09)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
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
