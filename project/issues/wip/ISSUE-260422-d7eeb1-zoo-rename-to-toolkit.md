# ISSUE-d7eeb1: Migrate marcel-zoo from integrations/ to toolkit/ (Phase 3 of 3c1534)

**Status:** WIP
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

## Implementation Approach

### Cross-repo shape

The code change lives entirely in `/home/shbunder/projects/marcel-zoo`. The marcel-core issue branch carries only the issue file (wip → closed lifecycle + Implementation Log entries). No marcel-core source changes.

The zoo is a standalone git repo. Because `ea6d47` confirmed the kernel walks `toolkit/` already, and `integrations/` still works under a deprecation warning, each habitat migrates independently. Migration strategy inside the zoo: one commit per habitat on a zoo branch `issue/d7eeb1-rename-to-toolkit`, then merge to zoo `main`. The marcel-core Implementation Log records the zoo-side SHAs for traceability.

### Field naming — `dispatch_type`, not `trigger_type`

[[ISSUE-ea6d47]] shipped today and renamed the spec field: templates gain an optional `dispatch_type:` key (default `agent`). The tasks below use `dispatch_type` everywhere the issue body still says `trigger_type`.

### Files to modify (all in marcel-zoo)

Per habitat `<name>` in `{docker, news, banking, icloud}`:

- `integrations/<name>/` → `toolkit/<name>/` — `git mv` the whole directory.
- `toolkit/<name>/integration.yaml` → `toolkit/<name>/toolkit.yaml` — `git mv`; content unchanged.
- `toolkit/<name>/__init__.py` — `from marcel_core.plugin import register` → `marcel_tool`; `@register("…")` → `@marcel_tool("…")`; replace any `integration.yaml` strings in docstrings with `toolkit.yaml`.
- `toolkit/<name>/README.md` — vocabulary updates (`@register` → `@marcel_tool`, `integration.yaml` → `toolkit.yaml`).
- `toolkit/<name>/tests/*.py` — comment-only references to `@register` / `integration.yaml` updated; no runtime-behaviour change required since `register` is still exported as an alias of `marcel_tool`.

Outside per-habitat:

- `jobs/*/template.yaml` — **discretionary:** the issue body asks for explicit `dispatch_type: agent` on every template. Because `plugin/jobs.py` treats a missing key as `agent`, this is pure cosmetic. Skip unless a template switches to `dispatch_type: tool`. None of the four templates (`sync`, `check`, `scrape`, `digest`) makes sense as a pure tool call today — each composes data + LLM narration — so none migrate to `tool` here. Task marked as "considered, not applied".

### Existing code to reuse (kernel side — unchanged)

- `marcel_core.toolkit.discover()` — `src/marcel_core/toolkit/__init__.py:253` — already walks both `toolkit/` and `integrations/`. Confirmed by kernel-side inspection: `toolkit/` wins on precedence; `integrations/` emits a single deprecation log.
- `register = marcel_tool` alias at `src/marcel_core/toolkit/__init__.py:198` — `@register` stays valid throughout the migration; the rename is purely vocabulary and won't break partial-migrated states mid-work.

### Verification steps

- Zoo-side: `uv run pytest` inside each migrated habitat's `tests/` — each habitat's tests green after migration.
- Kernel-side: from marcel-core, `make check` with `MARCEL_ZOO_DIR=/home/shbunder/projects/marcel-zoo` — 1411+ tests green; no deprecation log for the migrated habitats.
- Grep gate (run from zoo root):
  - `grep -rn "@register\b" --exclude-dir=__pycache__ .` — only historical doc comments in test files (acceptable as long as no active decorator usage remains), ideally zero hits.
  - `grep -rn "integration\.yaml" --exclude-dir=__pycache__ .` — zero hits.
  - `grep -rln "from marcel_core.plugin import register" --exclude-dir=__pycache__ .` — zero hits.
  - `test -d integrations || echo clean` — `clean`.

### Non-scope

- **Per-habitat `pyproject.toml` + UDS isolation** — deferred to [[ISSUE-14b034]] (UDS Phase 2). This issue only renames.
- **Zoo root `pyproject.toml` dep split** — same; stays monolithic here.
- **Channel habitat (`telegram`)** — it lives under `channels/`, not `integrations/`, so untouched by this rename.
- **Subagent habitats (`agents/`)** — markdown-only, no vocabulary impact.

## Tasks (one sub-task per habitat — each is its own marcel-zoo commit)

- [ ] `docker/` — rename dir + YAML, `@register` → `@marcel_tool`
- [ ] `news/` — same
- [ ] `banking/` — same
- [ ] `icloud/` — same
- [ ] Job templates reviewed for `dispatch_type: tool` migration (discretionary — none applied in this pass)
- [ ] Zoo-side tests green; kernel `make check` against renamed zoo green
- [ ] Grep verification (no `@register` decorator usage, no `integration.yaml`, no `integrations/` dir)
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped; kernel aliases support both names)
- Coordinates with: [[ISSUE-ea6d47]] (Phase 2 jobs dispatch_type — shipped; unlocks `dispatch_type: tool` for zoo jobs)
- Blocks: [[ISSUE-14b034]] (UDS Phase 2) — migrate under new structure. Both can proceed in parallel per habitat; they touch different lines.
- Blocks: [[ISSUE-807a26]] (UDS Phase 4 — `isolation: uds` default) and ISSUE-3c1534's Phase 5 alias removal.

## Implementation Log
<!-- issue-task:log-append -->
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
