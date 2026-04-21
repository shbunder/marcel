# ISSUE-a7d69a: Job habitat format + template migration

**Status:** Closed
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

- [✓] Define `template.yaml` schema. Document in `docs/plugins.md` + [docs/jobs.md](../../docs/jobs.md).
- [✓] Create template loader — walks `<data_root>/jobs/*/template.yaml`, parses, validates, exposes via the same `TEMPLATES` dict API that `jobs/templates.py` exposed. *(Loader also walks `<MARCEL_ZOO_DIR>/jobs/*/template.yaml`; data root wins on collision.)*
- [✓] Port every current template (`sync`, `check`, `scrape`, and any others in [jobs/templates.py](../../src/marcel_core/jobs/templates.py)) into a YAML file under a new `~/.marcel/jobs/<name>/template.yaml`. *(Ported to `<MARCEL_ZOO_DIR>/jobs/{sync,check,scrape,digest}/template.yaml` in the zoo repo — zoo commit `6f90b63`.)*
- [✓] Update [jobs/templates.py](../../src/marcel_core/jobs/templates.py) to delegate to the loader — or delete the file entirely if no Python logic remains. *(Delegated; `__getattr__` keeps backward compat for any `from marcel_core.jobs.templates import TEMPLATES` reader.)*
- [✓] Decide: does kernel `jobs/templates.py` ship a minimal `sync` template as a last-resort fallback, or is the user fully dependent on the zoo being present? *(No fallback — consistent with other habitat types. Documented in `docs/jobs.md` and `docs/plugins.md`.)*
- [✓] Verify: `jobs` tool still works — listing templates, creating jobs from templates, running them on schedule. *(Listing via `job_templates` covered by scenario test; creation via `create_job(template='sync')` unchanged — the tool always accepted `template` as a recorded label and left field-composition to the agent.)*
- [ ] Optional: JOB.md teaching files that the `jobs` skill can cite when helping the user choose a template. Not required for the migration — can be added later. *(Intentionally deferred — optional, not in scope.)*
- [✓] Tests: a fake job habitat under a tmp data root loads correctly and creates a runnable job.
- [✓] Docs: update [docs/jobs.md](../../docs/jobs.md) with the habitat layout.

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
- `create_job(template='sync')` is unchanged — the tool always stored
  `template` as a recorded label and left field composition to the
  agent, so the loader migration is invisible from the tool layer.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 8/8 applicable tasks addressed; Task 7 (JOB.md teaching
  files) explicitly optional and intentionally deferred.
- Shortcuts found: none.
- Scope drift: none.
- Stragglers: none blocking. The verifier noted two follow-up
  candidates not in scope here:
  (a) `jobs/tool.py::job_templates` could emit a one-line hint when the
  template list is empty ("No templates configured. Set
  `MARCEL_ZOO_DIR` or add a `template.yaml` under
  `~/.marcel/jobs/<name>/`.") — the failure mode is friendlier in chat
  than pointing users at docs.
  (b) The zoo vs. data-root iterator asymmetry in `plugin/jobs.py` —
  zoo enumerates every subdir and relies on `_load_template_file` to
  no-op on missing YAML, whereas data root pre-filters on
  `template.yaml` presence so it can coexist with instance dirs. Both
  are correct; the asymmetry is a docstring-clarification candidate.

## Lessons Learned

### What worked well

- **Mirror the channel-plugin shape.** The channel habitat work (ISSUE-7d6b3f)
  had just established `_iter_..._dirs` / `_load_...` / `discover_...` as
  the idiom for file-backed habitat loaders. Reusing it meant the new
  `plugin/jobs.py` landed in one sitting with familiar error-isolation
  semantics (a broken habitat never aborts discovery of its siblings).
- **Module-level `__getattr__` for backward compat.** Rewriting
  `jobs/templates.py` as a thin accessor while still exposing a
  "live" `TEMPLATES` symbol kept the one in-tree caller (the tool)
  untouched and preserved the contract for any future reader that
  imports `TEMPLATES` directly. Per the verifier, the idiom is PEP 562
  and does *not* mask typos — `AttributeError` still fires on unknown
  attrs.
- **"Kernel ships content-free" was already documented.** Picking
  option (b) — no kernel fallback — required zero negotiation because
  every other habitat type (integrations, skills, channels) had already
  set the precedent. The docs-in-impl grep confirmed the decision
  propagated to both `docs/jobs.md` and `docs/plugins.md`.

### What to do differently

- **Write the tool-layer friendliness in the first pass.** The
  verifier flagged that an empty template list renders as just
  `**Available job templates:**\n` with no hint. Trivial to add a
  one-liner during the same impl commit; now it's a follow-up. Next
  time a habitat loader ships, write the "nothing configured" hint at
  the tool layer at the same time, not after the verifier notices.
- **Zoo vs. data-root iterator asymmetry.** The two enumerators do the
  same job but filter at different layers — a small docstring would
  have saved the verifier (and future me) a moment of "wait, why is
  this different?". Pair-check any "two directories, one loader" shape
  for symmetric filter semantics or symmetric docstrings.

### Patterns to reuse

- **The three-function loader shape**
  (`discover_X → _iter_source_dirs × N → _load_X_file`) with a
  dedicated `_REQUIRED_KEYS` tuple and per-habitat-skip-on-failure
  error handling. Drop-in for the agent surface coming next
  (ISSUE-e22176).
- **Zoo-dir precedence < data-root precedence.** Zoo ships the
  out-of-the-box set; data root overrides for the operator that wants
  to tweak a single template without forking the zoo. Same rule as
  skills. Keep consistent across habitat types.
- **Cold-read-no-cache for habitat loaders.** Templates are small and
  the set is bounded by operator file count — caching buys nothing
  and costs live-edit ergonomics during development.
- **Module-level `__getattr__` for "I replaced a dict with a
  function"** refactors. Preserves `from X import SYMBOL` callers
  without a deprecation shim or a whole-tree sweep.
