# ISSUE-ea6d47: Jobs gain `trigger_type: tool | subagent | agent` (Phase 2 of 3c1534)

**Status:** Open
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

## Tasks

- [ ] Extend `JobDefinition` pydantic model with `trigger_type` + tool/subagent fields
- [ ] Add `model_validator` enforcing shape consistency per `trigger_type`
- [ ] Extend `template.yaml` schema validator with the new field
- [ ] Implement `_fire_tool_job` — calls toolkit registry directly
- [ ] Implement `_fire_subagent_job` — invokes via delegate or subagent loader
- [ ] Refactor `execute_job_with_retries` to branch on `trigger_type`
- [ ] Update `docs/jobs.md` (new in ISSUE-71e905, Phase 4) with the three trigger types
- [ ] Add `tests/jobs/test_trigger_types.py` — tool dispatch, subagent dispatch, agent default, validation errors, back-compat (no trigger_type → agent)
- [ ] `make check` green (coverage target: ≥90 % on the new dispatch paths)
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped)
- Relevant in: [[ISSUE-d7eeb1]] (Phase 3 zoo rename) — real zoo jobs can adopt `trigger_type: tool` here
- Documented in: [[ISSUE-71e905]] (Phase 4 docs — new `docs/jobs.md` covers trigger_types)
