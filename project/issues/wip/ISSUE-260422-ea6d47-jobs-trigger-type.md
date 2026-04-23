# ISSUE-ea6d47: Jobs gain `trigger_type: tool | subagent | agent` (Phase 2 of 3c1534)

**Status:** WIP
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, jobs, plugin-system

## Capture

**Follow-up to [[ISSUE-3c1534]] Phase 2.** The five-habitat-taxonomy plan documents three dispatch shapes for scheduled jobs: `tool` (deterministic, no LLM), `subagent` (bounded LLM with own context), `agent` (current default — full main-agent turn). This issue adds the schema field and the two new dispatch paths.

**Resolved intent:** Extend `JobDefinition` (`src/marcel_core/jobs/models.py`) and the `template.yaml` schema with a `trigger_type` field defaulting to `agent` (preserves today's behaviour). The job executor (`src/marcel_core/jobs/executor.py`) gains `_fire_tool_job` (calls the toolkit registry directly) and `_fire_subagent_job` (invokes via the delegate mechanism). The existing main-agent path becomes `_fire_agent_job` and is taken when `trigger_type == 'agent'`.

The feature delivers real cost savings: scheduled RSS fetches, health polls, and other deterministic tasks stop paying LLM cost. Subagent jobs get bounded context for focused tasks (morning digest, weekly review) without the main agent's full skill set.

## Description

### Schema changes

**`JobDefinition`** gains:

```python
class JobDefinition(BaseModel):
    ...
    trigger_type: Literal['tool', 'subagent', 'agent'] = 'agent'

    # For trigger_type='tool':
    tool: str | None = None           # toolkit handler name (e.g. "docker.list")
    tool_params: dict = Field(default_factory=dict)

    # For trigger_type='subagent':
    subagent: str | None = None       # subagent name (resolved via agents loader)
    subagent_task: str | None = None  # task string, {user_slug} templated

    # system_prompt/task/model fields remain — only consulted for trigger_type='agent'
```

Validation (`model_validator`): at exactly one of {`tool`, `subagent`, `agent`} shape must be populated per `trigger_type`. Missing `tool:` when `trigger_type='tool'` → `ValidationError`.

**`template.yaml`** schema gains the same field. Existing zoo `template.yaml` files without `trigger_type` default to `agent` — no migration required for back-compat.

### Executor changes

```python
# src/marcel_core/jobs/executor.py

async def execute_job_with_retries(job, trigger_reason, *, user_slug):
    ...
    if job.trigger_type == 'tool':
        run = await _fire_tool_job(job, trigger_reason, user_slug=slug)
    elif job.trigger_type == 'subagent':
        run = await _fire_subagent_job(job, trigger_reason, user_slug=slug)
    else:
        run = await _fire_agent_job(job, trigger_reason, user_slug=slug)
    # Post-run bookkeeping (consecutive errors, notify, append_run) is shared
    ...
```

**`_fire_tool_job`** — simplest path:
- Look up handler via `marcel_core.toolkit.get_handler(job.tool)`.
- Call `await handler(job.tool_params, slug)`.
- Wrap result in a `JobRun` with `status=COMPLETED` (or `FAILED` on exception).
- No retry chain — tool handlers are supposed to be deterministic; retries are the tool's own concern.

**`_fire_subagent_job`** — harder:
- Load the subagent markdown via `agents.loader.load_agent(job.subagent)`.
- Spawn a pydantic-ai `Agent` with the subagent's system_prompt + tools + model.
- Run it against `job.subagent_task` (templated with `{user_slug}`).
- Wrap the subagent's final output in a `JobRun`.
- Inherits retry + local-LLM fallback from the existing chain machinery where appropriate.

**`_fire_agent_job`** — current path, unchanged.

### Non-scope

- Adding a `trigger_type: channel` for jobs that fire a channel message directly (out of scope — channels are bidirectional transports, not dispatch targets).
- Allowing jobs to chain multiple trigger_types in sequence (e.g. tool → subagent). If this is ever needed, it becomes a new `trigger_type: pipeline` in a later issue.
- Exposing subagent invocation via `create_job` agent tool (separate feature — this issue is about the scheduler, not the agent-facing API).

## Implementation Approach

### Naming decision — `dispatch_type`, not `trigger_type`

The issue spec proposed `trigger_type`. The Phase-1 codebase already has `TriggerSpec.type` (enum `cron`/`interval`/`event`/`oneshot`) accessed as `job.trigger.type`. A top-level `job.trigger_type` field with a **different** value domain (`tool`/`subagent`/`agent`) would read as a variant of the existing concept and mislead every reader. Using `dispatch_type` reads as an orthogonal axis — "**when** does the job fire" (trigger) vs "**how** does it dispatch its work" (dispatch_type). Field semantics and shape are otherwise exactly as spec'd.

### Files to modify

- `src/marcel_core/jobs/models.py` — add `JobDispatchType` enum; extend `JobDefinition` with `dispatch_type`, `tool`, `tool_params`, `subagent`, `subagent_task`; add `@model_validator(mode='after')` enforcing shape per `dispatch_type`. Default `dispatch_type = AGENT` preserves today's behaviour for every persisted job.
- `src/marcel_core/jobs/executor.py` — split the current `execute_job_with_retries` body into `_fire_agent_job` (existing chain/pinned path); add `_fire_tool_job` and `_fire_subagent_job`; branch at the top of `execute_job_with_retries` on `dispatch_type`. Post-run bookkeeping (consecutive_errors, notify, append_run) stays shared below the branch.
- `src/marcel_core/plugin/jobs.py` — extend `_load_template_file` / schema docstring: accept optional `dispatch_type`; if `dispatch_type == 'tool'`, require `tool:`; if `dispatch_type == 'subagent'`, require `subagent:`. Absence → `agent` (back-compat).
- `tests/jobs/test_dispatch_types.py` — new unit tests covering all three paths, validator errors, and back-compat.

### Existing code to reuse

- `marcel_core.toolkit.get_handler(tool_name)` — `src/marcel_core/toolkit/__init__.py:201` — canonical handler-registry lookup; `_fire_tool_job` calls it directly with `job.tool_params`.
- `marcel_core.agents.loader.load_agent(name)` — `src/marcel_core/agents/loader.py:223` — loads an `AgentDoc` with frontmatter (model, tools, disallowed_tools, max_requests, timeout_seconds, system_prompt). Raises `AgentNotFoundError` on miss.
- `marcel_core.harness.agent.create_marcel_agent` + tier-sentinel resolution — pattern in `marcel_core.tools.delegate.delegate` at `src/marcel_core/tools/delegate.py:125-210` — `_fire_subagent_job` mirrors that flow (fresh `MarcelDeps` with derived `conversation_id` + fresh `TurnState`, tool filter resolved from `agent_doc`, `asyncio.wait_for` under `agent_doc.timeout_seconds`).
- `classify_error` / `humanize_error` — `src/marcel_core/jobs/executor.py:41` / re-exported at `:30` — reused for category assignment on tool/subagent failures so the chain-agnostic paths produce the same `run.error_category` values existing telemetry expects.
- `append_run` + `save_job` + `_notify_if_needed` — `src/marcel_core/jobs/executor.py:559-562` in the current `execute_job_with_retries` — stays *after* the dispatch branch so notify/retention/delivery are identical across dispatch types.
- `_resolve_run_user` — `src/marcel_core/jobs/executor.py:105` — resolves `user_slug` in every new dispatch function (single-user, system, or explicit).

### Verification steps

- New tests: `uv run pytest tests/jobs/test_dispatch_types.py -v` — all green.
- Regression: `uv run pytest tests/jobs/ -q` — existing executor tests unchanged (default `dispatch_type=agent` preserves legacy behaviour).
- Validator edge: `uv run pytest tests/jobs/test_dispatch_types.py::test_validator_rejects_tool_without_tool_name -v` — `ValidationError` raised.
- Back-compat edge: `uv run pytest tests/jobs/test_dispatch_types.py::test_backcompat_no_dispatch_type_defaults_agent -v` — `JobDefinition` parsed from a Phase-1-era dict dispatches through `_fire_agent_job`.
- `make check` — green with coverage ≥90% overall.

### Non-scope

- `docs/jobs.md` — authored in [[ISSUE-71e905]] (Phase 4 docs rewrite). This issue ships code + docstrings; the dedicated page lives with the taxonomy-wide rewrite so vocabulary stays consistent.
- Subagent chain/retries — `_fire_subagent_job` does **not** use `_run_with_backoff` or the model-chain machinery. Subagent jobs are expected to be bounded focused tasks, not long-running reliability-critical work. Can be relaxed in a follow-up if a real subagent job needs it.

## Tasks

- [ ] Extend `JobDefinition` pydantic model with `dispatch_type` + tool/subagent fields
- [ ] Add `model_validator` enforcing shape consistency per `dispatch_type`
- [ ] Extend `template.yaml` schema validator with the new field
- [ ] Implement `_fire_tool_job` — calls toolkit registry directly
- [ ] Implement `_fire_subagent_job` — invokes via subagent loader + create_marcel_agent
- [ ] Refactor `execute_job_with_retries` to branch on `dispatch_type`
- [ ] Add `tests/jobs/test_dispatch_types.py` — tool dispatch, subagent dispatch, agent default, validation errors, back-compat
- [ ] `make check` green (coverage target: ≥90 % on the new dispatch paths)
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped)
- Relevant in: [[ISSUE-d7eeb1]] (Phase 3 zoo rename) — real zoo jobs can adopt `trigger_type: tool` here
- Documented in: [[ISSUE-71e905]] (Phase 4 docs — new `docs/jobs.md` covers trigger_types)
