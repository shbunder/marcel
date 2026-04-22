# ISSUE-807a26: Remove the inprocess habitat path — single-pattern end state (Phase 4 of f60b09)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Low (blocked on Phase 2 + 3 completion)
**Labels:** refactor, plugin-system, cleanup

## Capture

**Final phase of [[ISSUE-f60b09]].** After Phases 2 + 3 migrate every python-carrying habitat to `isolation: uds`, the kernel's inprocess loader path is dead code. This issue deletes it, enforces `isolation: uds` schema-wide, and prunes the transitional scaffolding — delivering on the user's original "single pattern" commitment.

**Resolved intent:** `_load_external_integration` (the inprocess path), `_declared_isolation` (the fork), and the in-process branch of `discover()` all go away. Every python habitat must declare `isolation: uds` (or omit the key and get UDS by default — schema change from "default inprocess" to "default UDS"). The loader becomes a single code path. Markdown-only habitats (skills, agents) are unaffected — they never had a python loader to remove.

## Description

### What gets deleted

In `src/marcel_core/skills/integrations/__init__.py`:

- `_load_external_integration()` + `_EXTERNAL_MODULE_PREFIX` — the inprocess spec_from_file_location path
- `_declared_isolation()` — no longer needed when there's only one mode
- The `inprocess` branch in `discover()` — collapses to a single loop calling `_load_uds_habitat`
- Any test fixtures that exercise `isolation: inprocess` — updated or removed

In `scripts/zoo-setup.sh`:

- The flat root-`pyproject.toml` install path — every habitat now has its own `pyproject.toml`

### Schema change

`integration.yaml` / `channel.yaml` either:

- Make `isolation: uds` the default (current) and keep the key for forward-compat with future modes, OR
- Remove the key entirely — UDS is the only option.

Prefer the first: the `isolation:` key stays in the schema as an extension point (room for a future `wasm:` or `container:` mode), but its only valid value in Phase 4 is `uds`. The loader rejects anything else with a clear error.

### Zoo migration

Any habitat still declaring `isolation: inprocess` at the start of Phase 4 is broken by this change. A pre-Phase-4 audit: grep the zoo for `isolation: inprocess` and ensure Phase 2/3 migrated everything. This issue is blocked on that grep returning clean.

### What stays

- UDS bridge, supervisor, proxy — all load-bearing
- `@register` decorator — still how habitats declare handlers
- `integration.yaml` schema (name, description, provides, requires, scheduled_jobs) — unchanged
- Skill + agent markdown loaders — orthogonal

## Tasks

- [ ] Pre-flight: grep `<MARCEL_ZOO_DIR>/` and every known downstream zoo for `isolation: inprocess`. Non-empty → block this issue until Phase 2/3 finishes migrating.
- [ ] Delete `_load_external_integration`, `_declared_isolation`, `_EXTERNAL_MODULE_PREFIX` from `skills/integrations/__init__.py`
- [ ] Collapse `discover()` to a single loop calling `_load_uds_habitat`
- [ ] Rename `_load_uds_habitat` → `_load_habitat` (no more fork; the "uds" adjective is implicit)
- [ ] Update schema: `isolation:` defaults to `uds`; reject any other value with a clear error
- [ ] Delete the inprocess test path (`tests/core/test_plugin.py` covers the deleted inprocess loader today — migrate its assertions to UDS or delete)
- [ ] Update `docs/plugins.md`: "Isolation modes" section becomes "Habitat runtime" — one mode, no table
- [ ] Update `scripts/zoo-setup.sh`: drop the flat root-pyproject install path
- [ ] Document the schema change in a zoo migration note for downstream forks
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Non-scope

- Removing the `isolation:` key entirely (prefer keeping it for future `wasm:`/`container:` modes)
- Deprecating `@register` in favour of a class-based habitat API (separate design discussion)
- Third-party-habitat security model (capabilities, credential scoping) — relevant the day a family runs an untrusted zoo, not before

## Relationships

- Follows: [[ISSUE-931b3f]] (Phase 3 — channels/jobs must migrate first)
- Follows: [[ISSUE-14b034]] (Phase 2 — integrations must migrate first)
- Closes: [[ISSUE-f60b09]]'s "single pattern" commitment (Phase 4 is the commitment's fulfilment, not an option)
