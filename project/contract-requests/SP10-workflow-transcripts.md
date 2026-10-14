# Contract request from SP10 (a workflow's agents in the transcript stream)

Against `contracts/runner-api.yaml` (`GET /sessions/{session_id}/tail`) and `contracts/transcript.schema.json`,
and the app's `TaskEventType` that follows it. The runner cannot show a workflow's steps without these: the
layout is in `runner/tests/fake_claude/fixtures/transcripts/workflow-layout/README.md` (recorded on the NUC,
Claude Code 2.1.296).

**Why today's contract cannot.** A workflow agent writes `<session-id>/subagents/workflows/<run id>/agent-<hex>.jsonl`,
a plain subagent `<session-id>/subagents/agent-<hex>.jsonl`. `tail?subagent=<hex>` names neither the run nor the
folder, and the hub only opens a subagent tail on a `subagent_start` event, which a workflow agent never produces:
its agents are named only in the run's `journal.jsonl`. No `TranscriptEvent.type` can say "a workflow started",
"its agent N started", or "it finished".

## Proposed

1. **New events** in `transcript.schema.json` (and the same `type`s in app-api `TaskEventType`):
   - `workflow_start` `{tool_use_id, run_id, task_id, name}` from the main transcript: the `Workflow` tool's
     `tool_result` whose line has `toolUseResult.status: "async_launched"`, `taskType: "local_workflow"`.
   - `workflow_stop` `{tool_use_id, run_id?, status}` from the later `<task-notification>` user line with that
     `tool-use-id` (`completed`, `failed`, …).
   - `workflow_agent_start` `{run_id, agent_id, label, phase}` and `workflow_agent_stop` `{run_id, agent_id,
     summary}` from the run's `journal.jsonl` (`started` / `result`; `summary` cut like `subagent_stop`'s, 2 KB).
2. **The tail names a run.** `GET /sessions/{id}/tail?workflow=<run id>` streams the run's `journal.jsonl` as
   the `workflow_agent_*` events; `?workflow=<run id>&subagent=<hex>` streams that agent's own file, its events
   carrying `subagent_id` and a new `workflow_id`. Same offsets, resume and "newest tail wins" rules as today, one
   file per stream. `run_id` pattern `^wf_[A-Za-z0-9_-]{1,64}$`, `subagent` stays `^[0-9a-f]+$`; the resolved
   path must stay inside the session's folder (no `..`, no symlink out), else 400 `bad_request`.
3. **The hub** opens `?workflow=` on `workflow_start` and one `?workflow=&subagent=` per `workflow_agent_start`,
   like it does for subagents today (a story after the contract, hub lane).

## Alternatives considered

- `tail?subagent=<hex>` searching both folders: no contract change for the file, but the hub still never learns
  the agent ids (no `subagent_start`), so the app would show nothing. Not enough on its own.
- Emitting `subagent_start` for workflow agents from the journal: reuses an event whose `tool_use_id` and
  `description` mean something else for workflow agents, and still needs the run id to open the file.

## Not in scope

The run's `workflows/<run id>.json` (totals, tokens) and the script file: nothing in the app needs them yet.
