# ISSUE-d7eeb1: Migrate marcel-zoo from integrations/ to toolkit/ (Phase 3 of 3c1534)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, marcel-zoo, cross-repo

## Capture

**Follow-up to [[ISSUE-3c1534]] Phase 3.** The kernel-side rename + back-compat aliases shipped (Phase 1). This issue migrates the four existing zoo integrations to the new names so Phase 5's alias removal can proceed cleanly.

**Important:** Work lives in `~/projects/marcel-zoo` (separate git repo), not in marcel-core. One PR per habitat; merge order doesn't matter because the kernel walks both `integrations/` and `toolkit/`.

**Resolved intent:** For each existing integration habitat in marcel-zoo:

1. Rename directory: `integrations/<name>/` → `toolkit/<name>/`.
2. Rename YAML file: `integration.yaml` → `toolkit.yaml`.
3. Update imports in the habitat's `__init__.py`: `from marcel_core.plugin import register` → `from marcel_core.plugin import marcel_tool`.
4. Rename decorator usage: `@register("...")` → `@marcel_tool("...")`.
5. Verify `make check` from the kernel against the migrated zoo.

Zoo jobs gain explicit `trigger_type: agent` (back-compat-equivalent). Jobs better served by `trigger_type: tool` migrate here too (after [[ISSUE-ea6d47]] ships the executor changes).

## Description

### Per-habitat migration

Each habitat migrates independently:

- `integrations/docker/` → `toolkit/docker/`
- `integrations/icloud/` → `toolkit/icloud/`
- `integrations/news/` → `toolkit/news/`
- `integrations/banking/` → `toolkit/banking/`

Inside each:

```python
# Before:
from marcel_core.plugin import register

@register("docker.list")
async def list_containers(params, user_slug): ...

# After:
from marcel_core.plugin import marcel_tool

@marcel_tool("docker.list")
async def list_containers(params, user_slug): ...
```

```yaml
# toolkit.yaml (was integration.yaml, content unchanged)
name: docker
description: Manage docker containers on the home NUC
provides:
  - docker.list
  - docker.status
requires:
  ...
```

### Zoo root pyproject.toml

Shrinks as habitats migrate — but not in this issue. [[ISSUE-14b034]] (UDS Phase 2) lifts each habitat's deps into its own `pyproject.toml` as part of UDS isolation migration. This issue only renames the directory + yaml filename + decorator.

### Jobs

Existing zoo jobs under `jobs/<name>/template.yaml` gain explicit `trigger_type: agent`:

```yaml
# Before (implicit agent):
name: morning_digest
default_trigger:
  cron: "0 7 * * *"
system_prompt: ...

# After (explicit):
name: morning_digest
default_trigger:
  cron: "0 7 * * *"
trigger_type: agent          # explicit
system_prompt: ...
```

This is a no-op for scheduler behaviour (default is `agent`) but it's consistent with the Phase 2 schema and prepares the zoo for Phase 5 alias removal.

Any jobs better suited to `trigger_type: tool` (e.g. `docker_health_sweep` that today runs the full agent but only needs to call `docker.list` + format the output) migrate to the new type here. That's a discretionary step per habitat maintainer's judgment.

### Verification

After each habitat migrates:

- `make serve` — dev container boots, habitat loads under new name, kernel emits no deprecation warnings for that habitat.
- `make check` in marcel-core against the migrated zoo — no regressions.

After ALL four habitats migrate:

- Grep the zoo for `@register` — expected zero hits (all migrated to `@marcel_tool`).
- Grep the zoo for `integration.yaml` — expected zero hits.
- Grep the zoo for `integrations/` (as a path) — expected zero hits.

These are the preconditions for [[ISSUE-3c1534]] Phase 5 (alias removal).

## Tasks (one sub-task per habitat — each is its own marcel-zoo PR)

- [ ] `docker/` — rename dir + YAML, `@register` → `@marcel_tool`
- [ ] `icloud/` — same
- [ ] `news/` — same
- [ ] `banking/` — same
- [ ] Existing jobs gain `trigger_type: agent` explicit declaration
- [ ] Any jobs that make sense as `trigger_type: tool` are migrated (discretionary)
- [ ] Zoo-side `make check` green; kernel `make check` against renamed zoo green
- [ ] Grep verification (no `@register`, no `integration.yaml`, no `integrations/`)
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped; kernel aliases support both names)
- Coordinates with: [[ISSUE-ea6d47]] (Phase 2 jobs trigger_type — unlocks `trigger_type: tool` for zoo jobs)
- Blocks: [[ISSUE-14b034]] (UDS Phase 2) — migrate under new structure. Both can proceed in parallel per habitat; they touch different lines.
- Blocks: [[ISSUE-807a26]] (UDS Phase 4 — `isolation: uds` default) and ISSUE-3c1534's Phase 5 alias removal.
