# Claude Code Projects — the per-wave check (ADR-011)

At the start of every wave the lead reads the Claude Code Projects docs
(https://code.claude.com/docs/en/claude-projects) and adds one dated line: what changed, and whether any of
ADR-011's "when we switch" conditions is now met.

- 2026-10-13: public beta on Pro and Max, gradual rollout. Local threads (Remote Control) get the project
  instructions but not project memory, and stop when the machine sleeps. No API or event feed. One user. The docs
  mention desktop notifications only. None of the switch conditions is met.
