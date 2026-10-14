# workflow-layout — where a workflow writes its transcripts (recorded on the NUC, SP10)

Claude Code 2.1.296, a background session (`haiku`, `auto`) that used the `Workflow` tool over 10 files. Paths are
changed from `/home/shbunder` to `/home/marcel`. One of the ten agents is kept.

```
~/.claude/projects/<cwd>/
  <session-id>.jsonl                                   the session's own transcript
  <session-id>/workflows/<run id>.json                 the run: runId, taskId, status, agentCount, totalTokens …
  <session-id>/workflows/scripts/<name>-<run id>.js    the script (not kept here)
  <session-id>/subagents/workflows/<run id>/
    journal.jsonl                                      launched / started {agentId, label, phase} / result {agentId, result}
    agent-<hex>.jsonl                                  one per agent, named like a plain subagent's
    agent-<hex>.meta.json                              {"agentType": "workflow-subagent", "description", "workflowPhase", …}
```

What ties them together:

- **Main → run.** The `Workflow` `tool_use` (`toolu_…`) gets a `tool_result` saying "Workflow launched in background.
  Task ID: <task id> … Transcript dir: …/subagents/workflows/<run id>", and the line's `toolUseResult` holds
  `{"status": "async_launched", "taskId", "taskType": "local_workflow", "workflowName", "runId", "transcriptDir"}`.
  The end is a later user line `<task-notification><task-id>…</task-id><tool-use-id>toolu_…</tool-use-id>
  <status>completed</status>…`.
- **Run → agent.** `journal.jsonl` names each agent (`started` / `result` with `agentId`), and every line of
  `agent-<hex>.jsonl` carries `agentId` and `"isSidechain": true` and the parent's `sessionId`.
- **No `subagent_start` in the main transcript** for workflow agents, so a tail that waits for one never opens them.
- **Same id as a plain subagent?** Not seen; ids are 17 hex characters in a separate folder. A plain subagent's file is
  `<session-id>/subagents/agent-<hex>.jsonl`, a workflow agent's is `…/subagents/workflows/<run id>/agent-<hex>.jsonl`, so
  `tail?subagent=<hex>` alone cannot say which folder; it needs the run id too (or a search of both).
