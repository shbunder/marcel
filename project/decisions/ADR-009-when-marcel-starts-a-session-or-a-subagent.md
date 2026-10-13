---
id: ADR-009
title: When Marcel answers, starts a session, or uses a subagent
status: Accepted
date: 2026-10-13
decided_by: owner
evidence: Owner, 2026-10-13 (chat); Claude Code docs (sub-agents, agent-view, workflows, routines, cross-session-messaging, costs), read 2026-10-13
---

# ADR-009 — When Marcel answers, starts a session, or uses a subagent

## Context

Claude Code now has several ways to hand off work: subagents (the Agent tool), background sessions (`claude --bg`),
cloud sessions, dynamic workflows, skills that fork their context, routines, `/loop` and Monitor, and agent teams.
All of them spend the same subscription. The owner asked for a clear balance between "a new session" and "a subagent
inside a session". The docs give no thresholds; the numbers below are ours and S-16.2 measures them.

## Decision

| Marcel does this | When |
|---|---|
| **Answers itself** (brain turn) | A lookup or a short answer: about 2 minutes or less, no files changed |
| **Starts a quest** (`claude --bg` on the NUC, own worktree) | Repo work, anything longer than about 10 minutes, anything the owner may want to watch, steer or approve, anything that must survive a brain restart |
| **Starts a cloud quest** (`claude --cloud`) | Needs only GitHub, nothing on the NUC, and may run while the NUC is busy or off |
| **Subagent inside a quest** | The quest needs a side job whose summary is all that matters: search the codebase, review a diff, research a question |
| **Workflow inside a quest** | The same job on five or more independent items with one combined report (e.g. update 12 repos) |
| **Schedule** (the hub's scheduler, B-22) | Anything recurring. Routines stay out until the hub scheduler falls short |
| **Monitor inside a quest** | Watching a build, a deploy or a log while the quest keeps working |

Never:
- **A subagent or workflow in the brain.** Their results land in the brain's context and cost Opus turns; the brain
  routes, quests work.
- **Agent teams.** Experimental, off by default, and Marcel's hub already does the coordinating.

## Consequences

- The `orchestrate` skill states this table; `worker-protocol` tells quests they may use subagents, workflows and
  Monitor, and to report one step per subagent or workflow (S-21.1).
- Workers get `subagentPromptCacheTtl: 1h` so subagent calls reuse the cache like the main session.
- Parallel quests multiply usage; the usage guard (F10) and the worker cap still apply.
- SP10 checks on the NUC what the docs leave open: a reboot, a stalled approval, held messages, a workflow's cost.
