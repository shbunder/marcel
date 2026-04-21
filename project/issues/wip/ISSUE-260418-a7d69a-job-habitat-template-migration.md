# ISSUE-a7d69a: Job habitat format + template migration

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "The zoo habitats are jobs, integrations, skills, channels. They contain the markdown files and code required to specifically run them."

**Resolved intent:** Jobs today are partly kernel (scheduler, executor, tool, models, cache) and partly content (templates hardcoded in [jobs/templates.py](../../src/marcel_core/jobs/templates.py) as a Python dict). The kernel parts stay. The content parts — `sync`, `check`, `scrape`, and any other templates — become data-file habitats at `~/.marcel/jobs/<name>/`. This completes the habitat set: integrations, skills, channels, **jobs**, and agents.

## Description

**Job habitat layout:**

```
~/.marcel/jobs/<name>/
├── template.yaml        # description, default_trigger, system_prompt, task_template, notify, model
├── JOB.md               # optional agent-visible teaching: when to use this template
└── hook.py              # optional Python hook for complex setup (rare)
```

`template.yaml` mirrors the fields in the current [jobs/templates.py](../../src/marcel_core/jobs/templates.py) `TEMPLATES` dict. Field-for-field port — nothing new invented, just relocated to YAML so it's user-editable.

**Kernel job engine stays put:**
- [jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py)
- [jobs/executor.py](../../src/marcel_core/jobs/executor.py)
- [jobs/tool.py](../../src/marcel_core/jobs/tool.py) — the `jobs` tool the agent calls
- [jobs/models.py](../../src/marcel_core/jobs/models.py), [jobs/cache.py](../../src/marcel_core/jobs/cache.py)

**Template discovery:** a new loader walks `<data_root>/jobs/*/template.yaml`, parses it, and exposes the same dict shape that `jobs/templates.py` exposed before. Zero changes to downstream consumers — the `TEMPLATES` dict just sources from disk now.

**Integration-contributed scheduled jobs** (ISSUE-2ccc10's hook for banking-sync-every-8-hours) interoperate with this: an integration habitat's `integration.yaml` may declare `scheduled_jobs:` which the kernel auto-registers against the scheduler. Orthogonal to user-created jobs from templates; both feed the same scheduler.

## Tasks

- [ ] Define `template.yaml` schema. Document in `docs/plugins.md` + [docs/jobs.md](../../docs/jobs.md).
- [ ] Create template loader — walks `<data_root>/jobs/*/template.yaml`, parses, validates, exposes via the same `TEMPLATES` dict API that `jobs/templates.py` exposed.
- [ ] Port every current template (`sync`, `check`, `scrape`, and any others in [jobs/templates.py](../../src/marcel_core/jobs/templates.py)) into a YAML file under a new `~/.marcel/jobs/<name>/template.yaml`.
- [ ] Update [jobs/templates.py](../../src/marcel_core/jobs/templates.py) to delegate to the loader — or delete the file entirely if no Python logic remains.
- [ ] Decide: does kernel `jobs/templates.py` ship a minimal `sync` template as a last-resort fallback, or is the user fully dependent on the zoo being present? I'd lean the latter for consistency with other habitats — documented in `docs/jobs.md`.
- [ ] Verify: `jobs` tool still works — listing templates, creating jobs from templates, running them on schedule.
- [ ] Optional: JOB.md teaching files that the `jobs` skill can cite when helping the user choose a template. Not required for the migration — can be added later.
- [ ] Tests: a fake job habitat under a tmp data root loads correctly and creates a runnable job.
- [ ] Docs: update [docs/jobs.md](../../docs/jobs.md) with the habitat layout.

## Relationships

- Depends on: ISSUE-3c87dd (plugin discovery pattern)
- Integrates with: ISSUE-2ccc10 (integration-contributed scheduled jobs share the scheduler)
- Blocks: ISSUE-63a946 (zoo repo extraction)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-21 — habitat loader + kernel delegation + zoo port

**What shipped**

- New kernel module `src/marcel_core/plugin/jobs.py` with
  `discover_templates()` walking `<MARCEL_ZOO_DIR>/jobs/*/template.yaml`
  then `<data_root>/jobs/*/template.yaml` (data root wins on collision).
  Required keys (`description`, `system_prompt`, `notify`, `model`)
  validated at parse time; a broken habitat is logged and skipped so
  one bad YAML never aborts discovery of its siblings.
- `src/marcel_core/jobs/templates.py` rewritten as a thin accessor:
  `get_template` / `list_templates` / `TEMPLATES` (via `__getattr__`)
  delegate to the loader on every call — cold read, no cache, live
  edits reflected without a restart. Kernel ships no hardcoded
  templates and no fallback.
- Zoo-side ports at `<MARCEL_ZOO_DIR>/jobs/{sync,check,scrape,digest}/template.yaml`
  — field-for-field equivalents of the old Python dict.
- Plugin surface `marcel_core.plugin` re-exports `discover_templates`.
- Tests rewritten in `tests/jobs/test_cache_templates.py` to drive the
  disk loader: empty-when-no-sources, data-root-overrides-zoo,
  missing-required-key-skipped, instance-dir-ignored.
  `tests/jobs/test_tool_scenarios.py::TestJobTemplatesTool` now writes
  fake habitats into the tmp data root.
- Docs: `docs/jobs.md` Architecture + Templates + "Adding a new
  template" rewritten around the habitat loader. `docs/plugins.md`
  adds a full "Job habitat" section (schema table, minimal example,
  discovery semantics, no-fallback rationale) and the status note
  updated (job surface landed; only the agent surface remains).

**Verification**

- `make check` green, coverage 91.37%.
- Manual smoke: `discover_templates()` against the live zoo returns
  `{check, digest, scrape, sync}` with field values matching the old
  Python dict.

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
