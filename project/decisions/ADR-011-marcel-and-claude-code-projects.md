---
id: ADR-011
title: Marcel and Claude Code Projects — what Marcel adds, what it borrows, when to switch
status: Accepted
date: 2026-10-13
decided_by: lead, on the owner's "Claude Code first" direction (ADR-010)
evidence: Claude Code docs (claude-projects, agent-view, remote-control, channels), read 2026-10-13; research page https://claude.ai/artifact/D5jUrSk2eKu4LEQiTiui81 (2026-10-13)
---

# ADR-011 — Marcel and Claude Code Projects

## Context

Anthropic now ships **Claude Code Projects** (public beta on Pro and Max, rolling out gradually). Projects has one
coordinator conversation per project. The coordinator starts parallel "threads" (full sessions, in the cloud by
default, or on your own machine through Remote Control). It keeps project memory as files under a `MEMORY.md` index,
and shows threads on an Overview pane: Ready for review, Waiting on you, Working, Landing, Idle, Resolved. It runs on
claude.ai/code, the desktop app and the iOS app.

This is close to Marcel's core idea. Under ADR-010 ("if Claude Code does it, Marcel uses it"), Marcel has to say what
it adds, what it takes from Projects, and when it would stop building its own.

## Options

| | Option | Verdict |
|---|---|---|
| A | Build on Projects now: the app becomes a skin over Projects | No API or event feed to build on; cloud threads can't see the NUC; one user; still rolling out |
| **B** | **Keep Marcel; borrow Projects' vocabulary and brief; re-check at every wave** | **Chosen** |
| C | Ignore Projects | Against ADR-010: we would miss when it catches up |

## Decision

Option B.

**What Marcel adds** (the reasons Marcel exists while Projects lacks them):

| Marcel | Projects today |
|---|---|
| One continuous conversation across **all** projects (B-01), filed into projects by the brain (S-06.6) | One coordinator per project |
| Work on the NUC with Marcel's plugin, skills, hooks and the owner's files and home services | Cloud threads don't see local skills, plugins or MCP servers; local threads get instructions but not project memory, and run only while that machine is awake |
| Family members, each with their own helper, chat and memory (ADR-006, F17) | Single user |
| Pixel avatars (ADR-008) and living artifacts and boards kept current by the hub (B-34, B-35) | Neither |
| Push to the iPhone for "needs you" (F08) | The docs mention desktop notifications only |
| Steady on a Max login: no beta flag, no gradual rollout | Public beta |

**What Marcel borrows:**

1. **Thread groups.** The app groups quests the way Projects' Overview does, derived from the existing task states and
   PR artifacts, with no new task state: **Needs you** (`needs_you`), **Working** (`queued`, `starting`, `working`,
   `silent`, `unknown`), **Ended** (`failed`, `stopped`). A `done` task goes by its `pr` artifacts: any `open` or
   `draft` PR → **Ready for review**; otherwise any `merged` PR → **Done**; otherwise (every PR `closed`) →
   **Ended**; no PR → **Done**. New story
   [S-12.14](../stories/S-12.14.md) changes `ios/Marcel/Map/` (project room) and nothing in `contracts/`.
2. **The project brief.** Every project's notes file (`memory/projects/<slug>.md`, B-33) starts with Projects' brief
   headings: **What it is for**, **Where the work happens** (repos, folders), **How to check the work** (the one
   check command), **What needs a go-ahead first**, then **Notes** (what quests append through `report(note)`). The hub
   writes them when a project is made (`hub/src/marcel_hub/projects/store.py`, `_first_notes`); this joins
   [S-20.1](../stories/S-20.1.md), whose kickoff quest already writes the check command into the project notes. `worker-protocol` reads "How to check" and "What
   needs a go-ahead first" before starting.
3. **The coordinator sees reports, not every step.** Already Marcel's rule (the wake policy, S-06.5); this ADR
   records that Projects reached the same rule.

**When we switch to Projects.** The lead re-checks the Projects docs at the start of every wave and writes one line in
`project/lessons/claude-code-projects.md`. Marcel moves a part over to Projects when Projects has the matching
feature, in this order:

- Threads on the owner's own machine get the full local configuration (plugin, hooks, skills) **and** project memory,
  and survive the machine sleeping → the runner's worker spawning moves to Remote Control (`--spawn worktree`).
- An event feed or API that the hub can read (thread state, PRs, approvals) → the hub reads Projects instead of
  keeping its own task registry for those threads.
- One coordinator across several projects, more than one person, and iPhone push for "Waiting on you" → the brain
  and the app become a thin layer over Projects, and a new ADR replaces this one.

## Consequences

- No contract change.
- S-12.14 (new, Mac) and S-20.1 (one extra step) carry the borrowed parts.
- `project/lessons/claude-code-projects.md` holds the per-wave check; the first entry is this ADR's date.
- If Projects is enabled on the owner's account, the owner may use it next to Marcel for cloud-only repo work; Marcel
  does not try to track those threads.
