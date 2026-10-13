---
name: self-improve
description: How Marcel changes its own code (the hub, the plugin, the brain's instructions) - only as a task on the shbunder/marcel repository that ends with a pull request the owner approves in the app and merges on GitHub. Use when the owner asks Marcel to fix or improve itself, or when a worker thread works in Marcel's own repository.
---

# Changing Marcel itself

Marcel may change its own code **only through a PR on its own repository**, `shbunder/marcel` (B-24,
ADR-005). Nothing else changes a running Marcel: no edit on the NUC, no restart, no redeploy by hand.

## For the brain

- A self-change is an ordinary task. Start it like any other, in the project for Marcel's own code, with
  the repository `shbunder/marcel` and a clear goal ("Make the hub answer faster at night").
- Tell the owner in plain words what will happen: a worker makes a PR, an approval card appears, and
  nothing changes until they approve it in the app **and** merge it on GitHub.
- Never promise the change is live before the hub says it redeployed.

## For the worker

1. **Branch.** Work on a new branch from `main`. Never commit to `main`, never force-push.
2. **Follow the repository's rules.** Read its `CLAUDE.md` and `.claude/rules/` first. Keep the change
   small: one fix or one improvement per PR.
3. **Test.** Run the lane gate for every part you touched (`make -C hub check`, and so on) and fix what
   fails. Add a test for what you changed.
4. **Open the PR** on `shbunder/marcel`, against `main`, with a title the owner understands on a phone
   ("Hub: answer faster at night") and a short plain-words description: what changes for the owner,
   and how you tested it.
5. **Hand it in** with `report(artifact)`: kind `pr`, the PR's title and its address
   (`https://github.com/shbunder/marcel/pull/<number>`). The hub sees from the address that this is a
   self-change and opens the approval card itself: "Marcel wants to change itself: <PR title>", with
   the size of the change. You do not flag it.
6. **Finish** with `report(done)` and a summary that names the PR.

## Never

- **Never merge the PR**, not even when the owner approved it. The owner merges on GitHub.
- **Never restart or redeploy anything**: no `docker restart`, no `systemctl restart`, no running the
  redeploy script, no writing the redeploy flag. After the owner merges, the hub asks for the redeploy
  itself, and the host rolls it back on its own if the health check fails.
- Never point an approved PR's artifact at another PR. A new PR needs a new approval.
- An approval covers one commit. If you push again after the owner approved, Marcel asks the owner
  again, so push everything before you finish. Open the PR against `main`: nothing else redeploys.

## If the owner says no

The hub sends you a follow-up: the owner declined the change. Do not merge it and do not push to it
again. If your git credentials allow it, close the PR with a comment that the owner declined it (and
their reason, if the follow-up gives one). Then `report(done)`. If you cannot close it, say so in the
summary: the owner can close it on GitHub.
