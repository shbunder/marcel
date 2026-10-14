# SP10 — What background sessions do on a bad day — RESULT

**Verdict: PASS for steps 2–5; step 1 waits** (NUC, 2026-10-14, Claude Code 2.1.296). Step 1 needs a reboot, which
waits for the owner's `sudo loginctl enable-linger` (without it nothing comes back at boot; see S-14.1).

## 1. A quest across a reboot

Not run yet: it needs linger, then a reboot drill (S-14.1). To do then: start a quest, reboot, read
`claude agents --json --all` and the task's state; the hub should mark it failed and say so (S-05.8's
`ended_without_report` covers a session that ends; a reboot may not run the `SessionEnd` hook).

## 2. An approval left unanswered for an hour

An adopted session in `manual` mode was asked (through the hub) to write a file; its `Write` card opened at 00:03 UTC
and was left alone. At 01:04: the card was still `open`, the session still `blocked` on its permission prompt
(`waiting_for: permission prompt`), the task `needs_you`. Approving it then through `POST /api/approvals/{id}` worked:
`approved`, and the file was written. **Nothing times out within an hour**; a late answer still lands.

## 3. Worker → brain messages, with and without `crossSessionInbound: accept`

Two receivers (`--settings` with `"crossSessionInbound": "accept"` and `"refuse"`) and a sender that used
`ListAgents` and `SendMessage` to each:

- **accept:** delivered as `<cross-session-message from="uds:…" from-name="sp10-sender" from-mode="prompting">`; the
  receiver answered it.
- **refuse:** the sender's `SendMessage` reported the message refused ("It never reached that session's Claude").
  Nothing reached the receiver.
- **Nothing is held** in either case: delivered at once, or refused with the sender told. The brain and workers
  (`crossSessionInbound: refuse`, ADR-003, S-03.10) never see a worker's message; the hub sees everything.
- Seen in passing: each background session reports it is "also connected via Remote Control" (the owner's claude.ai).

## 4. A workflow over 10 files, against one plain quest

Same task (summarise 10 small files into one file), `haiku`, `auto` mode, one plain session and one using `Workflow`
(with the worker's role settings: `Workflow` allowed, `subagentPromptCacheTtl: 1h`):

| | assistant turns | output tokens | cache read | cache written |
|---|---|---|---|---|
| plain | 22 | 5.4k | 915k | 29k |
| workflow (lead session + its 10 agents) | 9 + 33 | 7.9k | 1.09M | 239k |

- `/usage` (whole percents) went from 6 % to 7 % of the five-hour window for both runs together; it is too coarse to
  tell one quest from another. The transcripts are the measure.
- Each workflow agent writes its own cache prefix: about 8× the cache writes of the plain run for a small job. Worth it
  for large parallel work, not for ten small reads.
- **The workflow's agents write their transcripts under `<session>/subagents/workflows/<wf id>/agent-*.jsonl`**, which
  the runner's tail does not follow (it follows `subagents/agent-*.jsonl` after a `subagent_start`). The app would not
  show a workflow's steps.

## 5. Deleting a session with uncommitted work

- In a git repo, `claude --bg` started at the **main checkout** runs in **its own new worktree**
  (`.claude/worktrees/<name>`, branch `worktree-<name>`); the checkout stays untouched. Started **inside a worktree**
  (as the runner starts a coding quest, `marcel/<task>-<slug>`), it works there, with no nested worktree.
- `claude rm <id>` on a session whose worktree has uncommitted changes **refuses**: "kept … The worktree has
  uncommitted changes. Deleting it would lose them." With the changes committed it **still refuses** while the commits
  are on no remote ("2 unpushed commits … deleting the worktree would lose them") and prints the exact
  `--discard-unpushed <commit>@<worktree-id>` value that would discard them. The work is lost only with that value, or
  by deleting the session in `claude agents` (Ctrl+X twice).
- The runner never calls `claude rm`. It removes only a worktree it has just made for a spawn that failed
  (`git worktree remove --force`, nothing in it yet) and the temporary `.cloud` folder of a cloud start.

## What changes in the plan

- **Follow-up (runner, S-03.x):** tail workflow agents too (`subagents/workflows/<wf>/agent-*.jsonl`), so a quest's
  workflow steps show in the app.
- **Follow-up (runner/hub):** if the runner ever deletes sessions or worktrees with work in them, use `claude rm`
  (which refuses) and never `--discard-unpushed` without the owner's yes; today it does neither.
- **Note for the brain's routing (ADR-009):** a workflow costs several times a plain quest's cache writes; keep it
  for large parallel work.
- Step 1 after linger.
