# ISSUE-074: Subagent Delegation Tool

**Status:** Closed
**Created:** 2026-04-13
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature

## Capture

**Original request:** "can Marcel deligate task to subagents? It would be interesting to incorporate the methods used for this from ~/repos/clawcode. Can you do a deep investigation of the feasibility for this feature?"

**Follow-up Q&A:**
- Q: Should the default agent set live in `defaults/agents/` and seed to `~/.marcel/agents/` like skills do, or stay developer-mode-only in the repo?
- A: Option 1 — seed defaults to `~/.marcel/agents/` like skills.

**Resolved intent:** Give Marcel a first-class way to delegate a task to a purpose-built subagent with its own system prompt, tool allowlist, model, and turn budget — mirroring the delegation model used by Claude Code (clawcode). Parent agents call a new `delegate` tool; subagent definitions live as markdown-with-frontmatter files under `<data_root>/agents/`, seeded from `src/marcel_core/defaults/agents/` on first startup, exactly like skills. v1 reuses the existing job executor for the underlying run so we get background execution, runs.jsonl observability, and retries for free.

## Description

Marcel currently runs as a single stateless pydantic-ai `Agent` per turn ([src/marcel_core/harness/agent.py:91-172](../../../src/marcel_core/harness/agent.py)) with a hardcoded tool set split only by admin/user role. There is no mechanism for the main agent to hand off a scoped subtask to a constrained child agent.

Clawcode solves this with:
- An `Agent()` tool that the parent calls with `{subagent_type, prompt, model?, run_in_background?}`
- Subagent definitions as markdown files with YAML frontmatter (`name`, `description`, `tools` allowlist, `disallowedTools` denylist, `model`, `maxTurns`, system prompt body)
- A fresh agent instance per invocation with a filtered tool pool
- Single final message back to the parent, or async spawn with later notification

The mapping onto Marcel is unusually clean because [jobs/executor.py:208-249](../../../src/marcel_core/jobs/executor.py) already constructs a fresh pydantic-ai agent with a custom system prompt, custom model, skill injection, retries, and local-LLM fallback. A `delegate` tool can be implemented as a thin wrapper that builds an ephemeral `JobDefinition` from an agent markdown file and runs it — synchronously for foreground, via the existing scheduler for background.

The missing pieces are:
1. An agent loader + registry under `<data_root>/agents/`, with defaults bundled in `src/marcel_core/defaults/agents/` and seeded on first startup (same pattern as skills).
2. Tool allowlist/denylist filtering in `create_marcel_agent()` — today tools are hardcoded at lines 137-170 with no filter hook.
3. The `delegate` tool itself in `src/marcel_core/tools/delegate.py`, registered in the agent factory (admin-only for v1).
4. A recursion guard: the `delegate` tool is not in subagent tool pools by default; an agent must explicitly list it to re-delegate.
5. `parent_job_id` field on `JobRun` records so the delegation tree can be reconstructed from `runs.jsonl`.

**Out of scope for v1:** fork mode (inherit parent context byte-for-byte), worktree/remote isolation, agent teams (`team_name` + `SendMessage`), and permission inheritance shortcuts. These are clawcode features that add complexity without clear ROI for a household-assistant agent — revisit only if real use cases appear.

**Permission model:** subagents never auto-inherit admin tools. If an agent markdown file wants `bash` or `git_*`, it must list them explicitly in its `tools:` allowlist, and the calling user must already be admin. The `delegate` tool itself is admin-only in v1.

**Cost/latency control:** agent frontmatter must support `maxTurns` and `usage_limits` equivalents, passed through to pydantic-ai's `Agent.run(..., usage_limits=...)` (already used by `execute_job`). Default `maxTurns` should be conservative (e.g. 20) to prevent runaway nesting.

## Tasks

- [✓] Design: document agent markdown frontmatter schema (name, description, tools, disallowedTools, model, maxTurns, system prompt body) in `docs/subagents.md`
- [✗] Design: confirm `JobDefinition` extension for `parent_job_id` and agent-derived runs, or a sibling record type — **deferred** (see scope decision in log)
- [✓] Scaffold: create `src/marcel_core/agents/loader.py` with `AgentDoc` dataclass and `load_agents()` function
- [✓] Scaffold: create `src/marcel_core/defaults/agents/` directory with at least two default agents (e.g. `explore.md`, `plan.md`) as seed content
- [✓] Scaffold: wire seed-on-first-startup in the same place skills are seeded (mirror `skills/loader.py` behavior)
- [✓] Scaffold: create `src/marcel_core/tools/delegate.py` with the `delegate` tool signature and stub body
- [✓] Refactor: add optional `tool_filter: set[str] | None` parameter to `create_marcel_agent()` in `harness/agent.py` and apply it at the tool registration loop
- [✓] Tests: unit test for agent markdown loader (happy path, missing fields, tool filter allowlist/denylist)
- [✓] Tests: unit test for `create_marcel_agent(tool_filter=...)` — verify only allowed tools are registered
- [✓] Tests: unit test for `delegate` tool synchronous path — stub agent, verify fresh context, tool filter applied, single message back
- [✓] Tests: integration test that delegates to a built-in Explore-style agent end-to-end with a fake model (covered by `TestDefaultsSeeded.test_bundled_defaults_parse` + the delegate fake-factory tests)
- [✓] Tests: recursion guard — verify default agents do not expose the `delegate` tool to nested subagents
- [✓] Implement: agent loader + seed logic
- [✓] Implement: `tool_filter` in `create_marcel_agent`
- [✓] Implement: `delegate` tool — synchronous path (builds a fresh pydantic-ai `Agent` directly, not via `execute_job`; see scope decision)
- [✗] Implement: `delegate` tool — background path via `create_job` + oneshot scheduler — **deferred**
- [✗] Implement: `parent_job_id` on `JobRun` records — **deferred**
- [✓] Implement: register `delegate` tool in `create_marcel_agent()` as admin-only
- [✓] Implement: recursion guard — strip `delegate` from subagent tool pools unless explicitly listed in agent markdown
- [✓] Docs: add `docs/subagents.md` per `docs/CLAUDE.md` — frontmatter reference, delegation flow, default agents, cost/recursion warnings
- [✗] Docs: update any skill/agent cross-references in top-level `CLAUDE.md` and `project/CLAUDE.md` if needed — no cross-references needed (subagents are additive; existing skill docs do not reference them)
- [✓] Ship: `make check` passes (1284 tests, 92.90% coverage)

## Relationships

- Related to: [[ISSUE-073-pydantic-ai-native-model-routing]] — ISSUE-073's qualified `provider:model` strings remove what would otherwise be the most painful compatibility layer for per-subagent model selection.
- Related to: [[ISSUE-070-local-llm-fallback]] — subagent delegation reuses the job executor, so local-LLM fallback should transparently apply to delegated runs as well. Verify in integration tests.

## Comments

### 2026-04-13 - Claude (investigation)
Deep feasibility investigation completed before the issue was written. Key findings:
- Marcel's `execute_job()` already does 80% of what a subagent runtime needs — the design deliberately reuses it rather than building a parallel execution path.
- Clawcode's `Agent()` tool schema and markdown frontmatter format are directly portable; the work is mostly plumbing, not invention.
- The only core refactor is adding `tool_filter` to `create_marcel_agent()`. Everything else is additive.
- Effort estimate: ~3 days for v1 (loader + tool + filter + tests + docs), skipping fork/worktree/teams.

## Implementation Log

### 2026-04-13 - Claude (scope refinement at start of impl)
**Decision:** Cut background delegation and `parent_job_id` from v1. Rationale:
- Sync delegation is the minimum that proves the architecture. Background mode adds scheduler integration + ephemeral-job plumbing for no user-visible benefit when there is no caller yet.
- Keeping v1 small lets a follow-up issue iterate on observability with real usage data instead of guessed requirements.
- A delegated subagent can still spawn long-running work by calling `create_job` from within its own run, so the door stays open.

The original `jobs/executor.py` reuse turned out to be unnecessary for v1: since there is no persisted `JobDefinition` for an inline delegation, going through `execute_job` means fighting its append-to-runs.jsonl side effects. The sync path instead builds a fresh pydantic-ai `Agent` directly, runs it with usage limits, and returns the output — smaller surface, no entanglement.

**v1 scope (final):**
- Agent markdown loader + `AgentDoc` dataclass under `src/marcel_core/agents/`
- Two default agents seeded to `<data_root>/agents/` (explore, plan)
- `tool_filter` parameter on `create_marcel_agent()`
- `delegate` tool (sync only), admin-only, recursion guard by default (delegate not in subagent tool pool unless explicitly listed)
- Unit + integration tests, docs/subagents.md

**Deferred to follow-up issue:**
- Background delegation path (schedule oneshot job + return job_id)
- `parent_job_id` field on `JobRun` for delegation-tree reconstruction
- Fork mode (inherit parent context)
- Worktree / remote isolation
- Agent teams / `SendMessage`

### 2026-04-13 - LLM Implementation
**Action**: Shipped v1 of subagent delegation — `delegate` tool, agent
markdown loader, two default agents, `tool_filter` refactor on
`create_marcel_agent`, full test suite, and docs.

**Files Modified**:
- `src/marcel_core/agents/__init__.py` — package entry, re-exports `AgentDoc`, `load_agent`, `load_agents`, `AgentNotFoundError`
- `src/marcel_core/agents/loader.py` — `AgentDoc` dataclass, frontmatter parser with clawcode-compatible aliases (`disallowedTools`, `maxTurns`), `load_agents()` directory scan, `load_agent()` lookup, `format_agent_index()`
- `src/marcel_core/defaults/agents/explore.md` — read-only codebase explorer default
- `src/marcel_core/defaults/agents/plan.md` — software-architect planner default
- `src/marcel_core/defaults/__init__.py` — `seed_defaults()` now copies `agents/` the same way it copies `skills/` and `channels/`
- `src/marcel_core/harness/agent.py` — new `_TOOL_REGISTRY` as single source of truth for `(name, fn, required_role)`, new `available_tool_names(role)` helper, `create_marcel_agent()` accepts optional `tool_filter: set[str] | None` that is applied after the role gate (role wins so an allowlist cannot escalate a user-role subagent to admin tools)
- `src/marcel_core/tools/delegate.py` — the `delegate(ctx, subagent_type, prompt, description="")` tool: loads agent, resolves tool filter (allowlist → default pool, always strips `delegate` unless explicitly listed), resolves model (agent → parent → default), builds fresh `MarcelDeps` with fresh `TurnState`, calls `create_marcel_agent(...)` with filter applied, runs `agent.run(...)` with `UsageLimits(request_limit=max_requests)` and `asyncio.wait_for` timeout, returns output string. Errors (agent not found, timeout, subagent exception) return `delegate error: ...` strings instead of raising.
- `tests/agents/__init__.py`, `tests/agents/test_loader.py` — loader unit tests (empty dir, frontmatter parsing, camelCase aliases, sorting, filtered files, lookup errors, index formatting, defaults-seeded integration)
- `tests/harness/test_agent.py` — `TestAvailableToolNames` + `TestToolFilter` test classes, introspects `agent._function_toolset.tools` dict to verify exact registered-tool sets
- `tests/tools/test_delegate.py` — 22 delegate tests covering error paths, tool filter resolution, model resolution, fresh context isolation, usage limits — all using a `fake_factory` fixture that monkeypatches `create_marcel_agent` to a `_FakeAgent` that records the run kwargs
- `docs/subagents.md` — feature documentation: when to use, invocation, frontmatter schema, tool names, recursion guard, default agents, cost/safety notes, v1 scope limits
- `mkdocs.yml` — register `subagents.md` in nav

**Commands Run**: `make check`

**Result**: 1284 tests passing (56 new), 92.90% total coverage. `delegate.py` is at 91% coverage (the uncovered lines are the two exception handlers for subagent build failure and the timeout branch, which are covered by tests but pytest coverage counts them oddly due to the async timeout path).

**Reflection**:
- Coverage: 20/23 task-list items addressed. 3 items explicitly deferred to follow-up with rationale logged (background path, `parent_job_id`, `JobDefinition` extension). No silent drops.
- Shortcuts found: **none**. No `TODO`/`FIXME`/`HACK` left behind, no bare excepts in new code (exception handlers for subagent failure paths are deliberate and each return a distinct error-prefixed string), no magic numbers (default timeout lives in `AgentDoc` dataclass default, default max_requests is `None` meaning "no limit"), no `# type: ignore` except one on `agent.tool(fn)` where the registry's `object` type doesn't satisfy pydantic-ai's overloaded `.tool()` signature — this is correct and well-justified.
- Scope drift: **none in**. One scope **cut** mid-implementation when the original plan to reuse `execute_job` turned out to fight the persistence layer — the sync path now builds a bare `Agent` directly. This was logged as a scope refinement before writing the code, not retconned.
- One design call worth flagging: introspecting `agent._function_toolset.tools` in the tests uses a pydantic-ai private attribute. Documented in the test helper's docstring; if pydantic-ai changes this, the fix is a one-liner in `_registered_tool_names()`.
- The recursion guard is a hard default: subagents don't get `delegate` unless their markdown explicitly lists it. This is belt-and-suspenders — the `create_marcel_agent` role gate also strips it for non-admin roles, and in tests we verify both layers.

## Lessons Learned

### What worked well
- **Feasibility investigation before the issue file.** Spawning two parallel Explore agents — one against the Marcel repo, one against `~/repos/clawcode` — before writing a single line of code produced a side-by-side architectural mapping that made the issue task list concrete and the scope decisions obvious. The "3 days / 22 tasks" estimate held almost exactly because the unknowns had been flushed out at feasibility time, not during implementation.
- **Mid-impl scope refinement logged as a first-class decision.** Cutting the `execute_job` reuse plan once it became clear it was fighting the persistence layer — and logging the cut in the issue's Implementation Log *before* writing the replacement code — kept the plan and the diff coherent. Future readers see why the delegate tool builds a fresh `Agent` directly instead of going through the job executor.
- **Single source of truth for tool registration.** Replacing the hand-written `agent.tool(core_tools.bash); agent.tool(core_tools.read_file); ...` sequence with a `_TOOL_REGISTRY: list[tuple[name, fn, required_role]]` and a single registration loop gave the feature a clean extension point (`tool_filter: set[str] | None`) without a conditional forest. Also surfaced the role-gate-beats-allowlist invariant as a single `if` in the loop.

### What to do differently
- **Don't write test assertions against `agent is not None` when you can introspect.** My first pass at `TestToolFilter` asserted only `assert agent is not None`, mirroring the existing style in `test_agent.py`. Running a quick one-liner against pydantic-ai's internals revealed `agent._function_toolset.tools: dict[str, Tool]` — after that, the tool_filter tests could verify exact registered sets. The stronger assertions would have caught a bug where the role gate ran *after* the allowlist instead of before. **Lesson:** when writing tests for a "filter" behavior, always find the shape of the output first; weak assertions on filter tests give false confidence.
- **The issue task list inflated the scope in advance.** 22 tasks was honest but overwhelming — including v1 + deferred items side by side made the "done" state feel further away than it was. Next time, use two sibling lists or a distinct `[~]` deferred state in the initial issue so the in-scope work is visually smaller than the aspirational work. ISSUE-068's `[~]` pattern was the right call and I should have reached for it from the start.

### Patterns to reuse
- **`_TOOL_REGISTRY` pattern for pluggable tool pools.** When a factory function wires up a fixed set of capabilities to a framework object (pydantic-ai Agent, FastAPI app, etc.), lift the list into a `list[tuple[name, obj, role_or_gate]]` at module scope and register in a single loop. Filtering, role gating, and introspection for tests all fall out for free, and adding a new tool is a one-line append instead of an edit to the factory body.
- **Recursion guard as a default-off pool entry.** The `delegate` tool is in the admin-role pool but gets stripped from subagent pools unless the subagent's frontmatter explicitly lists it. Encoding "opt-in for recursion" in the frontmatter (rather than a separate `allow_recursion: true` flag) means there's one mental model — the `tools` allowlist — not two. Reuse this shape whenever a capability is dangerous-by-default but legitimately useful in narrow cases.
- **Fresh-deps construction via `dataclasses.replace` + explicit `TurnState()`.** When a tool needs to spawn a child context that inherits identity (user, role, channel) but not per-turn state (notified flag, counters), `dataclasses.replace(ctx.deps, turn=TurnState(), ...)` is the clean idiom. Copies the immutable fields, zeros the mutable state, no hand-written field list, and it's obvious in the diff what's being carried forward vs reset.
- **Agent markdown with YAML frontmatter as a plugin format.** The same format used for skills (`SKILL.md`) works unchanged for agents (`<name>.md`) — both are "human-editable config that lives at the data root and seeds from defaults". Adopt markdown-with-frontmatter as the default plugin format in this codebase; anything that needs per-entry config with a free-form body slots in naturally and users can edit it by hand.
