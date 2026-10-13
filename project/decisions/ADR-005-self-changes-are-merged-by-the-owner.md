---
id: ADR-005
title: Self-changes are merged by the owner on GitHub, never by the hub
status: Accepted
date: 2026-10-13
decided_by: lead
evidence: ADR-002; .claude/rules/self-modification.md; S-11.1 (project/stories/S-11.1.md, hub/tests/test_selfmod.py)
---

# ADR-005 — Self-changes are merged by the owner on GitHub, never by the hub

## Context

B-24 and S-11.1 said "Approve → merge through the GitHub API (squash, no force) → `request_redeploy(sha)`", and
"deny closes the card and comments on the PR". Both need a GitHub token that can write. But:

- ADR-002 keeps `MARCEL_GITHUB_TOKEN` read-only and says "Self-change PRs (F11) are merged by the owner on
  GitHub, not by this token".
- `.claude/rules/self-modification.md` forbids "a PR merged by Marcel itself": a human approves and merges.

## Options

| | Option | Verdict |
|---|---|---|
| A | Give the hub a write token; approve merges through the API | Breaks ADR-002 and the self-modification rule; a leaked or misused token could merge any change to Marcel |
| **B** | **Approve in the app is the owner's go-ahead; the owner merges on GitHub; the hub redeploys only a PR that is both approved in the app and merged** | **Chosen.** Two separate acts by the owner; the token stays read-only |

## Decision

Option B.

- **Approve** on the self-change card records the approval and tells the owner, in plain words, to open the PR
  and merge it on GitHub (the PR address and `marcel://artifact/<id>`). The hub keeps watching the PR.
- **Merged.** When the PR refresh (`github.py`, S-04.12) sees the PR merged *and* its card was approved in the
  app for that same PR address, the hub calls `request_redeploy(sha)`: it writes the merge commit to the redeploy
  flag, the only legal restart path. The SHA comes only from GitHub's answer for the merged PR
  (`merge_commit_sha`, 40 hex characters), never from a worker or the owner.
- **Merged without that approval** (never answered, denied, answered in a terminal, or approved for another PR
  address): no redeploy, and one plain alert — "A change to Marcel was merged without approval in the app; not
  redeploying."
- **Deny** records the denial, and the card's note says the owner may close the PR on GitHub. The read-only token
  cannot comment on or close the PR, so the hub sends the task that made it a follow-up (`steer`, author
  `marcel`): do not merge, close the PR with a comment if your own git credentials allow it.
  This deviates from the lead's first wording ("tell the brain with a system event"): `SystemEvent.kind` has no
  self-change kind, so a `steer` to the worker is the only in-contract way. A new kind is asked for in
  `project/contract-requests/S-11.1.md`.
- The hub flags a PR as a self-change from its address alone (`https://github.com/shbunder/marcel/pull/<n>`):
  workers cannot set the flag (the channel `Artifact` has no field for it) and cannot avoid it.

## Consequences

- B-24 and S-11.1 are amended: approving no longer merges; the owner merges on GitHub.
- The owner does two things for a self-change: approve in the app, merge on GitHub. Merging alone redeploys
  nothing.
- Commits pushed to the PR after the approval are not re-approved in the app; the owner sees the final diff on
  GitHub when merging, which is the real gate.
- The hub's GitHub token stays read-only (ADR-002 unchanged).
- The redeploy flag lives at `redeploy_flag` (default `/state/redeploy.flag`); S-11.2 / S-14.1 mount the host's
  `~/marcel/state` at `/state`.
