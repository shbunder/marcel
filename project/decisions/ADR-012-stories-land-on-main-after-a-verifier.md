---
id: ADR-012
title: Stories land on main after a fresh verifier; a PR only when the owner must review
status: Accepted
date: 2026-10-14
decided_by: owner ("only use PRs if there is something really important for me to review, otherwise I want speed"), applied by the lead
evidence: the last 8 story PRs (#86–#93) were merged by agents with no human review; CI on every push to main since 2026-10-14 (.github/workflows/check.yml)
---

# ADR-012 — Stories land on main after a fresh verifier; a PR only when the owner must review

## Context

Every story ended in a PR that the lead merged with `/merge-story`. In practice nobody but agents read those
PRs: the review was the `story-verifier`, and the PR only added a wait. The owner asked for speed and to see a
PR only when it needs them. Since 2026-10-14 GitHub runs every lane's gate on each push to `main`, so a
change that lands without a PR is still tested.

## Options

| | Option | Verdict |
|---|---|---|
| A | Keep a PR for every story | Safe but slow; the owner never reads most of them |
| **B** | **Land on `main` after the gate and a fresh `story-verifier` "merge" verdict; open a PR only for what the owner must see** | **Chosen.** Same checks, no wait; the owner's attention goes where it matters |
| C | Push straight to `main` with no verifier | Fastest; loses the only independent check |

## Decision

Option B. `/finish-story` lands the story itself (a `--no-ff` merge commit on `main`, CI watched until
green) unless the story needs the owner. It opens a PR instead when the change:

- touches the self-change path: `deploy/redeploy.sh`, `deploy/health.sh` (the rollback decides on it),
  `deploy/systemd/marcel-redeploy.*`, `deploy/tests/test_redeploy.sh`, `hub/src/marcel_hub/selfmod.py`,
  `hub/src/marcel_hub/watchdog/`, or any code that restarts, redeploys, rolls back or moves the owner's live install
  (for example `marcel update`, `marcel migrate`). Narrowed by the owner on 2026-10-16: other `deploy/` files
  (install scripts, compose, settings files, other units) land after the verifier like any story.
- rests on a decision with a real trade-off the owner has not made (an ADR that would be `Proposed`);
- changes how something looks or reads to the family (the app's screens, the avatar, the icon, a
  notification's wording) and its "done when" asks for the owner's sign-off.

`/merge-story` stays for those PRs, typed by a person (it is not model-invocable).

## Consequences

- `Done` on the board is set by `/finish-story` when it lands, or by `/merge-story` for a PR. `Review` is
  only used while a PR waits for the owner.
- A red `check` on `main` is fixed before anything else lands; the session that landed it owns the fix.
- Runtime self-changes by Marcel itself are not affected: they are PRs the owner approves and merges
  (ADR-005, `.claude/rules/self-modification.md`).
- Branch protection is not on (owner, 2026-10-14). If it is added later it must let the NUC's rollback push
  its revert to `main`.
