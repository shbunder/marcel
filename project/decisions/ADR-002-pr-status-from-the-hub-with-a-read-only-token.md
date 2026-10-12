---
id: ADR-002
title: The hub reads PR status from GitHub with a read-only token
status: Accepted
date: 2026-10-12
decided_by: lead
evidence: S-04.7 review (PR #25); project/stories/S-04.7.md
---

# ADR-002 — The hub reads PR status from GitHub with a read-only token

## Context

A `pr` artifact shows its CI checks and review state (B-21, B-24), refreshed by the hub. S-04.7 says this runs
"through `gh api`, run by the runner on the host, or a GitHub token on the hub (decided in F02)", but F02 never
recorded a decision. S-04.7 shipped the interface and a tested translator for GitHub's answers, without wiring.

## Options

| | Option | Verdict |
|---|---|---|
| **A** | The hub calls GitHub's REST API with a **fine-grained, read-only** token (Pull requests: read, Checks/Commit statuses: read, on the owner's repos), from `deploy/.env.local` | **Chosen.** No new runner operation; works while the runner is down; the token cannot change anything |
| B | The runner runs `gh api` on the host with the owner's existing `gh` login, through a new runner-API operation | No new secret, but couples PR cards to the runner, needs a contract change, and the host `gh` login can write |
| C | Workers report PR status in `report(artifact)` | Goes stale as soon as the worker finishes; CI runs after |

## Decision

Option A. The token lives in `deploy/.env.local` as `MARCEL_GITHUB_TOKEN` (never in git, never logged, never sent to
the app). Without it, PR cards say plainly that the hub cannot ask GitHub, as they do today. Refresh: when a PR
artifact is created, then every 5 minutes while the PR is open, and once when it closes; one round of requests per PR
per refresh, with backoff on GitHub's rate-limit answers.

*Amended 2026-10-14 (S-04.12 review):* a round is three REST calls (the PR, its checks, its reviews), as S-04.12
asks for the REST API. That is about 36 calls an hour per open PR, inside GitHub's 5,000 an hour up to about 130 open
PRs, which is far beyond a home setup. One GraphQL query could replace the three if that ever matters.

## Consequences

- The owner creates one fine-grained read-only token when deploying (F14 notes the step).
- Self-change PRs (F11) are merged by the owner on GitHub, not by this token: it stays read-only.
- Wiring is story S-04.12.
