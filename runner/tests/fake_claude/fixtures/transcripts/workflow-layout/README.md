# workflow-layout — where a workflow's agents write their transcripts (recorded on the NUC, SP10)

Claude Code 2.1.296, a background session (`haiku`, `auto`) that used the `Workflow` tool over 10 files. The
session's own transcript is `<session-id>.jsonl`; each workflow agent writes
`<session-id>/subagents/workflows/<workflow id>/agent-<id>.jsonl`, with a `journal.jsonl` beside them. One of the
ten agents is kept here. The runner's tail follows `subagents/agent-*.jsonl` after a `subagent_start` and does not
look in `subagents/workflows/` (SP10 RESULT, follow-up). Paths are changed from `/home/shbunder` to `/home/marcel`.
