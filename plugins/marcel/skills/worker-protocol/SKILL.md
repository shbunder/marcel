---
name: worker-protocol
description: How a Marcel worker thread reports back. Use in every task Marcel started or adopted - to say you started, to report milestones with your remaining steps, to hand over results (PR links, files, dashboards), and always to finish with done or failed and a summary.
---

# Worker protocol

You are a **thread**: one Claude Code session that Marcel started for one piece of work. The owner
watches you in the Marcel app on their phone. Marcel's main chat shows only your milestones:
*started*, *needs you*, *done + summary + artifacts*, *failed + why* (B-06). Everything else you
send lives in your thread view.

You tell Marcel how it is going with one tool: `report`. Workers have no other Marcel tools: the
brain's tools answer a worker with an error.

Before you start, read your project's notes (they reach you at the start): follow **How to check the
work** and **What needs a go-ahead first**.

## Helpers inside your quest (ADR-009)

- A **subagent** (the Agent tool) for a side job whose summary is all you need: search the code,
  review a diff, research a question.
- A **workflow** for the same job on five or more like items with one combined report (update 12
  repos).
- **Monitor** to watch a build, a deploy or a log while you keep working.
- Report **one** `progress` step per subagent or workflow, not one per item inside it, so your quest
  view stays readable.

## The four rules

1. **Start:** call `report(started)` once, as soon as you understand the job.
2. **Milestones, not steps:** call `report(progress)` when something the owner would care about is
   finished, not for every command. Three to eight progress reports for a long task; none for a
   five-minute one. Give your remaining steps in `next`.
3. **Hand over what you made:** a PR, a branch, a file, a dashboard, a link. Attach it with
   `report(artifact)` when it is ready, or in `artifacts` on the final report.
4. **Always finish:** your last call is `report(done)` or `report(failed)`, with a `summary`. Never
   stop without it. If you are stuck, that is `failed` with what blocked you, not silence.

## The fields

| `kind` | Needs | Use for |
|---|---|---|
| `started` | nothing | You read the job and are starting. Optional `text`: your plan in one line. |
| `progress` | `text` | A milestone. Optional `next`: the steps you still plan, in order. |
| `artifact` | `artifact` | One thing you made, as soon as it exists. |
| `done` | `summary` | The work is finished. Optional `artifacts`: everything worth keeping. |
| `failed` | `summary` | You cannot finish. Say why and what would unblock it. |
| `note` | `text` | One lasting fact later threads in this project need ("The Luminus contract ends on 1 March 2027"), at most 1000 characters, 10 per task. It goes in the project's notes, not the chat. If Marcel refuses it, put the fact in your `done` summary instead. |

### `next` — your remaining steps

On `progress`, give your remaining steps in `next`: a list of short lines (at most 20, each at most
200 characters), in the order you plan them. Each progress report replaces the previous list, so
send the whole remaining plan each time and drop what is done. The app shows it as the quest's
step list.

```json
{"kind": "progress", "text": "Found the cause: the login test waits on a fixed 2 s timer.",
 "next": ["Replace the timer with a wait for the login response", "Run the test 50 times", "Open a PR"]}
```

### `note` — a fact for later threads

Send a note only when a later thread in this project would need the fact and could not easily
find it again: a date, a decision, where something lives, what did not work. Not your progress,
not what is already in a PR or a file you attach.

```json
{"kind": "note", "text": "The Luminus contract ends on 1 March 2027; the cancellation notice is one month."}
```

### Artifacts

An artifact is `{kind, title, url | path}`:

| Artifact `kind` | Give |
|---|---|
| `pr`, `branch`, `link` | `url`: the full URL. |
| `doc`, `file`, `dashboard` | `path`: an absolute path on this machine. A `doc` is Markdown or HTML; a `dashboard` is JSON in the dashboard format. |

- `title`: what it is, in a few words the owner will recognise ("Docker volumes on the NUC").
- `artifact_id`: when the prompt gave you one, pass it to publish a **new version** of that living
  artifact instead of a new one. Add `summary`: what changed in this version, in one line.
- Keep files you want to hand over inside your working folder. Do not attach secrets, `.env` files
  or anything with a token in it.

## The project's tracker (coding projects)

When your briefing says **Tracker:** with a claude.ai link, the project keeps a board of its quests
there (B-46). Keep your own card current with `ArtifactData` on that link, the doc
`cards/<your task id>`:

- **When you start** (with `report(started)`): `set` it to `{title, quest, status: "In Progress",
  updated_at, by: "Marcel"}`, where `title` is what the quest does in a few words and `quest` your
  quest's title.
- **When your PR is open:** `update` it with `status: "Review"`, `pr_url`, `ci` (`passing`, `failing`
  or `pending`) and `updated_at`. Set `waiting: true` when the owner has to review or decide.
- Each move also gets one `log` row: `add` `{at, card: "<your task id>", kind: "status", text:
  "In Progress" | "Review: PR #<n>", by: "Marcel"}`.
- Pin every `update` with `if_version` from your last read. Never touch another quest's card, and
  never set `Done`: the evening digest does that once the PR is merged.
- **The tracker is a convenience, never a reason to fail.** If you cannot reach it (no `ArtifactData`
  tool, an error, a version conflict twice), carry on with the quest and send one `report(note)`:
  "Could not update the tracker: <why>." A cloud quest has no tracker tools: skip it.

## How to write for the owner

Every `text` and `summary` is read on a phone, often by someone who is not an engineer.

- **Outcome first.** "Fixed: the login test no longer fails. PR #142 is ready." Then, if it helps,
  one line on how.
- **Short sentences, plain words.** No stack traces, no log dumps, no file lists. If a detail
  matters, name it once ("the 2 s timer in `login.spec.ts`").
- **Concrete numbers and names.** "38.2 GB in `plex_media_cache`", not "a large volume".
- **A failure says what is needed.** "Failed: the NUC has no GitHub token for `shbunder/harry`.
  Add one and say retry."

Good `done` summary:

> Five largest volumes listed; `plex_media_cache` is 38.2 GB. Dashboard attached.

Bad:

> I ran `docker system df -v` and parsed the output, then I sorted it and here are the results …

## Permission prompts and questions

- You run in auto mode. If a step needs the owner's approval, Claude Code asks and Marcel turns it
  into an approval card on their phone. Wait for it; do not look for a way around it.
- If you need a decision you cannot make, send `report(progress)` with the question in `text`, then
  wait for a message in your thread. The owner can type into your thread directly.

## A cloud thread (no `report` tool)

A thread in the cloud cannot reach Marcel's channel. Your prompt then gives you a report token.
Send the same fields as JSON with `POST https://marcel-bot.com/api/session/report` and the header
`Authorization: Bearer <token>`. The token works for this one task only, for 48 hours, and stops
working after your `done` or `failed`, so send that last. A cloud thread hands over `pr`, `branch`
and `link` artifacts only (a URL, never a path). A follow-up message from Marcel brings a new token:
use it from then on. Never write the token into a file, a commit or a PR.

## Example run

```
report(started)    text: "Looking for why the login test fails on CI."
report(progress)   text: "Found it: a fixed 2 s timer races the login call."
                   next: ["Wait for the response instead", "Run it 50 times", "Open a PR"]
report(progress)   text: "Fix works: 50 runs, 0 failures."   next: ["Open a PR"]
report(done)       summary: "Fixed the flaky login test. PR #142 is ready to merge."
                   artifacts: [{kind: pr, title: "Fix flaky login test", url: "https://github.com/shbunder/marcel/pull/142"}]
```
