# ISSUE-074: Subagent Delegation Tool

**Status:** Open
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

- [ ] Design: document agent markdown frontmatter schema (name, description, tools, disallowedTools, model, maxTurns, system prompt body) in `docs/subagents.md`
- [ ] Design: confirm `JobDefinition` extension for `parent_job_id` and agent-derived runs, or a sibling record type
- [ ] Scaffold: create `src/marcel_core/agents/loader.py` with `AgentDoc` dataclass and `load_agents()` function
- [ ] Scaffold: create `src/marcel_core/defaults/agents/` directory with at least two default agents (e.g. `explore.md`, `plan.md`) as seed content
- [ ] Scaffold: wire seed-on-first-startup in the same place skills are seeded (mirror `skills/loader.py` behavior)
- [ ] Scaffold: create `src/marcel_core/tools/delegate.py` with the `delegate` tool signature and stub body
- [ ] Refactor: add optional `tool_filter: set[str] | None` parameter to `create_marcel_agent()` in `harness/agent.py` and apply it at the tool registration loop
- [ ] Tests: unit test for agent markdown loader (happy path, missing fields, tool filter allowlist/denylist)
- [ ] Tests: unit test for `create_marcel_agent(tool_filter=...)` — verify only allowed tools are registered
- [ ] Tests: unit test for `delegate` tool synchronous path — stub agent, verify fresh context, tool filter applied, single message back
- [ ] Tests: integration test that delegates to a built-in Explore-style agent end-to-end with a fake model
- [ ] Tests: recursion guard — verify default agents do not expose the `delegate` tool to nested subagents
- [ ] Implement: agent loader + seed logic
- [ ] Implement: `tool_filter` in `create_marcel_agent`
- [ ] Implement: `delegate` tool — synchronous path first, building an ephemeral `JobDefinition` and calling `execute_job()`
- [ ] Implement: `delegate` tool — background path via `create_job` + oneshot scheduler, returning `job_id`
- [ ] Implement: `parent_job_id` on `JobRun` records, written by delegation path
- [ ] Implement: register `delegate` tool in `create_marcel_agent()` as admin-only
- [ ] Implement: recursion guard — strip `delegate` from subagent tool pools unless explicitly listed in agent markdown
- [ ] Docs: add `docs/subagents.md` per `docs/CLAUDE.md` — frontmatter reference, delegation flow, default agents, cost/recursion warnings
- [ ] Docs: update any skill/agent cross-references in top-level `CLAUDE.md` and `project/CLAUDE.md` if needed
- [ ] Ship: `make check` passes; version bump per `VERSIONING.md`

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
<!-- Append entries here when performing development work on this issue -->
