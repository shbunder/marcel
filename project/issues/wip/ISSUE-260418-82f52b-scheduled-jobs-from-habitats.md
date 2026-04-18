# ISSUE-82f52b: Scheduled jobs from integration habitats (plugin surface)

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, plugin-system, marcel-zoo, jobs

## Capture

**Original request:** "Design and land the 'integration habitat contributes a periodic job' hook on the marcel_core.plugin surface — sub-issue under ISSUE-2ccc10, prerequisite for the news + banking zoo migrations. Two candidate designs to choose between: **(a) declarative** — `integration.yaml` grows a `scheduled_jobs:` block listing `{name, cron, handler}` entries; the kernel's `jobs/scheduler.py` reads them at discovery time and registers them alongside in-tree jobs. **(b) imperative** — the integration's `__init__.py` exports a top-level `register_scheduled(scheduler)` function that the discovery loop calls once per habitat; the function uses scheduler primitives to add cron entries. Decide which one fits Marcel's 'lightweight over bloated, generic over specific' principles better, document the decision in `docs/plugins.md` (new 'Scheduled jobs from habitats' section), implement on the kernel side, prove it with a fake/test habitat (no need to migrate a real integration in this issue — that's news's job next), and verify a missing/malformed `scheduled_jobs:` entry rolls back the whole habitat (mirroring the directory-name ↔ handler-namespace error-isolation precedent from ISSUE-6ad5c7). The current banking sync is registered manually in `src/marcel_core/jobs/scheduler.py`; this issue does NOT migrate banking — just lands the hook so news (next sub-issue) can be the first habitat to use it. Closes the open task in ISSUE-2ccc10 line 42 ('Design the integration contributes a periodic job hook')."

**Resolved intent:** Land the kernel-side mechanism that lets a zoo integration habitat declare (or register) periodic jobs without touching kernel code. Today both first-party periodic jobs (banking sync, news sync) are wired manually in `src/marcel_core/jobs/scheduler.py` — that file becomes a closed door once `news` and `banking` move to the zoo. This issue picks one of the two candidate designs (declarative `integration.yaml: scheduled_jobs:` vs imperative `register_scheduled(scheduler)`), implements it, documents it on the stable `marcel_core.plugin` surface, and proves it end-to-end with a synthetic test habitat that registers a job, runs it once, and gets garbage-collected on uninstall. By the end of this issue: (1) the design choice is documented with the rationale, (2) `docs/plugins.md` has a "Scheduled jobs from habitats" section the next zoo author can follow, (3) the kernel scheduler picks up jobs from a habitat via the chosen mechanism, (4) malformed declarations roll the whole habitat back (no half-registered state — same precedent as ISSUE-6ad5c7's namespace check), and (5) a stub test habitat in `tests/` exercises the full path. The news migration (next sub-issue under ISSUE-2ccc10) consumes this hook; banking migration after that does the same.

## Description

The two designs trade off in classic Marcel ways:

**(a) Declarative — `integration.yaml: scheduled_jobs:`**
- Habitat declares `scheduled_jobs: [{name, cron, handler}, ...]`; kernel `jobs/scheduler.py` reads them at discovery time.
- Pro: data, not code — readable in the same `integration.yaml` next to `provides:` and `requires:`. Inspectable without import side effects. The "uninstall = remove directory" property holds cleanly: no scheduler entry survives because there's nothing to unregister beyond what discovery owns.
- Pro: validation lives in one place (the yaml schema check), failures roll back the habitat the same way the directory-name ↔ handler-namespace check does in ISSUE-6ad5c7.
- Con: requires the handler to already be in the habitat's `@register` set — `scheduled_jobs.handler` is just a name lookup. (Acceptable: every periodic job today is "call this integration handler on a cron".)
- Con: cron expressions only. No "register a python callable that doesn't fit the integration handler signature" path. (Acceptable: that's `register_scheduled` territory which we're explicitly choosing against.)

**(b) Imperative — `register_scheduled(scheduler)` in `__init__.py`**
- Habitat optionally exports a top-level `register_scheduled(scheduler)` function; discovery loop calls it once per habitat with a scoped scheduler primitive.
- Pro: maximum flexibility — habitats can register arbitrary callables, computed crons, conditional jobs.
- Con: code, not data — to know what jobs a habitat registers you have to read python, which can have side effects, raise, take arbitrary time at discovery. Worse audit story.
- Con: rollback is harder — if `register_scheduled` raises halfway through, the scheduler is in a partial state unless we make the scoped primitive transactional (extra plumbing).
- Con: violates "generic over specific" — every habitat reinvents its own cron registration; declarative is one shape they all share.

**Recommendation (subject to implementation review):** Pick **(a) declarative**. It is the smaller surface, the better audit story, and matches every concrete need we have today (banking sync, news sync — both are "call handler X on cron Y"). If a future habitat genuinely needs imperative registration, we add `register_scheduled` as a *second* mechanism then, not now. This is "generic over specific" + "lightweight over bloated" both pulling the same way. The implementation step below codifies the choice; if the design choice flips during implementation, the Implementation Log captures why.

**Plugin surface change:**

```yaml
# integration.yaml — new top-level key
name: example
provides: [example.fetch]
requires:
  credentials: [EXAMPLE_TOKEN]
scheduled_jobs:
  - name: example_hourly_fetch
    cron: "0 * * * *"          # croniter syntax — matches existing jobs/scheduler.py
    handler: example.fetch     # must be a name in `provides:`
    params: {}                 # optional — passed to handler as the params dict
```

The kernel `jobs/scheduler.py` registers each entry as if it were an in-tree scheduled job. Validation rules — any failure logs an error and rolls back the **entire integration** (not just the malformed entry, mirroring ISSUE-6ad5c7's namespace rule):

- `scheduled_jobs` must be a list of mappings.
- Each entry must have `name` (str), `cron` (valid croniter expression), `handler` (str matching an entry in `provides:`).
- `name` must be unique within the habitat *and* across the registry (collision with an existing scheduled job is a rollback condition).
- `params` is optional and must be a dict.

Discovery side: when the integration's `integration.yaml` parses cleanly *and* the handlers register cleanly *and* the `scheduled_jobs` validate, the scheduler entries are added. If any of those steps fails, the habitat is rolled back (handlers removed, scheduler entries removed) — same all-or-nothing principle as the namespace check.

**Why this issue does not migrate news:** news is the next sub-issue (after this one) under ISSUE-2ccc10. Migrating news in *this* issue would conflate "design and prove the hook" with "consume the hook for the first real integration." Those are two different review surfaces; splitting is the same move that split ISSUE-e7d127 (icloud migration) from ISSUE-c48967 (plugin credentials surface).

**Test strategy:**
- Synthetic test habitat under `tests/fixtures/test_habitats/scheduled/` (or wherever the existing zoo-fixture pattern lives — discover via grep) with a single handler and a `scheduled_jobs:` entry. Test asserts the entry shows up in the scheduler after discovery.
- Negative test: habitat with malformed `scheduled_jobs:` (handler not in `provides:`, invalid cron, duplicate name) — assert the habitat rolls back fully (no handler, no scheduler entry).
- Run-once test: scheduler invokes the registered handler with the right params dict and user_slug.

**Out of scope (explicit):**
- Migrating banking or news jobs to the new mechanism. Banking still wires its sync the old way after this issue closes; the news migration (next sub-issue) is when the kernel `jobs/scheduler.py` first stops being the registration site for a real periodic job.
- Changing the cron syntax or the scheduler's run model — only the *registration path* is new.
- Per-habitat scheduler isolation (each habitat gets its own scheduler instance). The kernel scheduler stays singleton; habitats just contribute entries.

## Tasks

- [ ] Read `src/marcel_core/jobs/scheduler.py` end-to-end. Note how in-tree jobs are registered today (decorator? function call? config file?) and where the croniter validation happens.
- [ ] Confirm or revise the (a)-vs-(b) recommendation based on what the scheduler code actually exposes. If the imperative path turns out to be ~3 lines vs declarative's ~30, flip the choice and document why in the Implementation Log.
- [ ] Extend the kernel `integration.yaml` parser (lives in `src/marcel_core/skills/integrations/__init__.py` per the docker POC) to accept and validate `scheduled_jobs:`.
- [ ] Wire the validated `scheduled_jobs` entries into `jobs/scheduler.py` at discovery time. Each entry registers as if it were an in-tree job.
- [ ] Implement habitat rollback on any `scheduled_jobs` validation failure: handlers removed from `_registry`, scheduler entries removed, `integration.yaml` metadata not registered. Match the precedent set in ISSUE-6ad5c7's namespace check.
- [ ] Add a synthetic test habitat under `tests/` exercising the happy path (one handler + one scheduled job, scheduler picks it up).
- [ ] Add negative tests for: handler-not-in-provides, invalid cron, duplicate job name within habitat, duplicate job name across habitats. Each must roll back the habitat fully.
- [ ] Add a run-once test that the scheduler invokes the registered handler with the declared `params` dict and the right user_slug.
- [ ] Add a "Scheduled jobs from habitats" section to `docs/plugins.md` showing the `integration.yaml` schema, the validation rules, the rollback behavior, and a worked example (use a fictional habitat — news + banking are still kernel-side at this point).
- [ ] Update the "What `marcel_core.plugin` exposes" section in `docs/plugins.md` if any new symbol gets re-exported from the plugin package (likely none — this is pure metadata, no python API).
- [ ] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the "Design the integration contributes a periodic job hook" task `[✓]` and link this issue from the Implementation Log. Add a follow-up note that news is now unblocked.
- [ ] `make check` green at the 90% coverage gate.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions + integration.yaml validation precedent — landed)
- Depends on: ISSUE-c48967 (marcel_core.plugin surface — landed)
- Part of: ISSUE-2ccc10 (umbrella tracker — closes the "scheduled-jobs hook design" task)
- Unblocks: news migration (next sub-issue under ISSUE-2ccc10), banking migration (after news)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-18 — Design decision: thick declarative

Read [src/marcel_core/jobs/scheduler.py](../../../src/marcel_core/jobs/scheduler.py), [models.py](../../../src/marcel_core/jobs/models.py), [__init__.py](../../../src/marcel_core/jobs/__init__.py), and the existing `_ensure_default_jobs()` pattern (banking sync). Confirmed the recommendation in the issue with one important refinement: Marcel's "periodic jobs" are not raw cron handlers — they are full **agent jobs** (`JobDefinition` with `system_prompt`, `task`, `model`, `skills`, `notify`, `channel`, dispatched through the LLM executor). The banking sync's `_ensure_default_jobs()` is the prior art: it creates a `JobDefinition` once, marks it with `template='sync'`, and `save_job()` persists it.

User reasoning for the choice (verbatim):

> "I would go for B [from the original issue framing], the idea was that some jobs are indeed determinstic and some require LLM creativity. I wanted to maintain a single pipeline as to make the setup clear and simple."

That framing maps to **thick declarative**: every habitat-declared `scheduled_jobs:` entry becomes a real `JobDefinition` (system-scope, `template='habitat:<name>'`), reusing the entire agent pipeline. Per-entry overrides for `task`, `system_prompt`, `model`, `notify`, `channel`, `timezone` allow the LLM-creative case to customize freely. The defaults give the deterministic "just call handler X on cron Y" case for free — Marcel synthesizes a system_prompt that asks the agent to call the handler and report.

Final schema landed in `integration.yaml`:

```yaml
scheduled_jobs:
  - name: "iCloud calendar sync"      # required
    cron: "0 */4 * * *"               # required (XOR with interval_seconds)
    handler: icloud.calendar          # required, must be in provides:
    params: {days_ahead: "30"}        # optional dict
    description: "..."                # optional
    notify: on_failure                # optional, default silent
    channel: telegram                 # optional, default telegram
    timezone: "Europe/Brussels"       # optional
    task: "..."                       # optional override
    system_prompt: "..."              # optional override
    model: "anthropic:..."            # optional override
```

Discovery flow: `_load_external_integration()` validates `scheduled_jobs:` strictly, **rolls back the entire habitat on any failure** (matching the namespace-check precedent from ISSUE-6ad5c7). Stable IDs (`sha256(f"{integration}:{entry.name}").hex[:12]`) let `_ensure_habitat_jobs()` reconcile across restarts: synthesize → save_if_missing → drop orphans whose habitat name no longer in `_metadata`.

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
