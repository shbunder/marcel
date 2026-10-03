# Marcel v3 — functional specification

This is the record of what Marcel v3 must do, written from the interview of 2026-10-03.
Every story in `features/` traces back to a numbered behaviour here (`B-…`). When a story
and this page disagree, this page wins until the owner changes it.

## In one sentence

Marcel is **one continuous conversation on my iPhone** with an agent that runs on my NUC,
starts and steers Claude Code sessions (on the NUC or in the cloud) to do the actual work,
and keeps me posted — with a living 3D giraffe at the top of the screen showing what it is doing.

## Who and what

| Decision | Answer |
|---|---|
| Users | **Only the owner in v1.** Later, each family member gets their own agent(s). |
| Agents | v1 ships **one agent, Marcel**. The data model treats an agent as a generic record (name, animal, palette, persona, memory, owner) so "several agents per person" or "one lead plus specialists" stays possible without a rewrite. |
| Work Marcel takes on | Coding on my repos · life admin · research and writing · home/NUC ops |
| Codebase | **Fresh start** in a monorepo (`shbunder/marcel`). The old code is kept on `legacy/v2`. |
| Old Marcel (family Telegram bot) | **Retired now.** The family loses it until they get their own agents. |
| Harry | Stays a separate service. Marcel uses it later as **one MCP connector among many**. |
| Billing | **Claude Max, subscription only.** Every token is spent by the unmodified `claude` CLI. There is no API key anywhere. |

## The conversation

- **B-01 One thread.** There is one main conversation per agent. It never resets from my point of
  view. Marcel's working context may be compacted or rolled over underneath; the transcript I see does not change.
- **B-02 Non-blocking.** I can send a new message while Marcel or any worker is busy. Marcel answers
  quick questions directly. Anything substantial becomes a **thread**.
- **B-03 Auto-start.** Marcel starts a thread immediately and tells me in one line what it started
  and where ("Started *Fix flaky login test* on the NUC"). It does not ask first. I can cancel.
- **B-04 Where it runs.** Marcel picks the location per task and names it:
  - **cloud** for work on GitHub repos that needs nothing local;
  - **NUC** for anything that needs local files, Docker, the home network, Harry, or the NUC's tools.
- **B-05 Follow-ups go to the right thread.** Marcel keeps a roster of active threads and forwards
  a follow-up ("also add tests to that") to the thread it belongs to. It starts a new thread only
  when none fits, and always says which one it chose.
- **B-06 Milestones only.** The main chat gets only milestones: *started*, *needs you*,
  *done + summary + artifacts*, *failed + why*. Detailed progress lives in the thread view and the
  Activity tab.
- **B-07 Status questions.** "What's going on?" or "where is the Harry thing?" gets an answer from the task
  registry. Marcel does not re-read transcripts to answer it.
- **B-08 Side threads (Slack-style).** I can **reply in thread** on any message, by long-press or a reply
  button. That opens a side conversation that sees the main context. The main conversation does not
  see the side thread unless I (or Marcel at my request) send a result back.
- **B-09 Text only in v1.** Voice comes later.

## Threads (worker sessions)

- **B-10 A thread is a Claude Code session.** On the NUC it is a background `claude` session, in a
  git worktree when it touches a repo. In the cloud it is a claude.ai/code session.
- **B-11 Watch and steer.** Opening a thread shows a **live transcript**: messages, tool calls and
  diffs. From there I can type into the thread directly, approve or deny a permission, stop it, or hand it
  back to Marcel.
- **B-12 Approvals.** Workers run in Claude Code **auto mode**. Whatever auto mode escalates becomes
  an **approval card**, shown in the thread and in the main chat and sent as a push notification. The card has
  approve and deny buttons. Answering it unblocks the worker.
- **B-13 Models.** Marcel's brain uses **Opus**. Workers default to **Sonnet**. Marcel may choose Opus
  for a task it judges hard, and I can override per task ("use opus for this").
- **B-14 Hand-over.** Sessions Marcel did not start are ignored unless I hand them over. I do that by running
  **`/marcel-adopt`** inside that Claude Code session. From then on Marcel tracks it like its own
  threads.
- **B-15 Concurrency cap.** There is a hard cap on concurrent NUC workers (default 4). Extra work queues
  and Marcel says so.

## Proactivity and notifications

- **B-16 Marcel may message me unprompted when:**
  - a task needs me;
  - a task finished;
  - something broke: a worker died or went silent, CI failed, the NUC or a service is down, or usage is near the limit;
  - a scheduled digest is due.
- **B-17 Digests:**
  - **Morning, plan of the day:** pending approvals, today's scheduled jobs, threads in flight,
    calendar highlights once a calendar connector exists.
  - **Evening, what got done:** finished threads, PRs ready, artifacts, failures.
  - **Usage report:** plan usage consumed, broken down by task.
  - **Weekly review:** a roll-up, plus stale threads to close or archive.
- **B-18 Push.** Notifications go to the iPhone through APNs when an Apple developer account exists. Until then
  they go through an **ntfy** fallback. The rest of the system must not care which one is used.

## Usage guard

- **B-19** When the Max usage window nears its limit, Marcel:
  - stops spawning new workers and queues new requests;
  - lets running workers finish;
  - tells me the reset time;
  - resumes the queue automatically after the reset.

## Memory and artifacts

- **B-20 Memory is editable Markdown.** `MEMORY.md` holds facts, preferences and commitments. Dated
  notes go in `memory/YYYY-MM-DD.md`. It is stored in git on the NUC. I can view and edit it in the app.
  Workers receive the slices relevant to their task.
- **B-21 Library.** Every thread's artifacts are collected per task and in one Library:
  - PRs and branches, with CI status;
  - docs and reports (Markdown or HTML);
  - files and media (PDF, images, screenshots);
  - live dashboards: structured widgets rendered natively in the app.

## Scheduling

- **B-22** Recurring work ("every Monday do X") is created in conversation. Marcel chooses where each
  job runs: NUC jobs fire from the hub's scheduler; cloud-able jobs spawn a cloud worker at fire time.
  Every schedule appears in one list in the app, and I can pause or delete it there.
- **B-23** If a scheduled job did not run, or a worker has been silent past its expected time, Marcel
  says so. Silence is never mistaken for success; this follows Harry's watchdog lesson.

## Self-modification

- **B-24** Marcel may improve its own code (hub, plugin, brain instructions) **only through a PR on its
  own repo**. The PR shows up as an approval card. Approving it merges the PR and redeploys through the flag-file
  mechanism, with a health check and an automatic `git revert` rollback.

## The app (iOS only, native Swift)

- **B-25 Tabs:**
  - **Chat** (main conversation, with the avatar on top);
  - **Activity** (one card per thread, **needs-you pinned on top**, then working, then done/failed);
  - **Library**;
  - **Memory**;
  - **Settings**.
- **B-26 Thread view.** This is the live transcript described in B-11. It opens from a milestone message, an Activity card or a push notification.
- **B-27 Access.** The app reaches the NUC hub through the Cloudflare tunnel (`marcel-bot.com`) and signs in
  with a device token, paired once with a QR code shown by the hub.

## The avatar

- **B-28 Living status indicator.** A 3D low-poly giraffe, faithful to the logo, sits at the top of
  the chat. It animates the agent's state:

  | State | Animation |
  |---|---|
  | idle | breathing and blinking |
  | thinking | head tilts, ossicones wobble |
  | working(n) | **n spots glow, one per active thread** |
  | needs-you | looks at the camera, orange pulse |
  | done | nod |

- **B-29 Made for you.** An avatar is generated, not modelled: **animal + palette + name/seed → avatar**,
  the same way every time (a 3D take on boring-avatars). v1 ships the giraffe with a few palettes. The builder accepts new
  animal recipes without code changes.

## v1 is done when

1. **Chat + spawn + report:** on the iPhone I ask, Marcel starts NUC and cloud threads, reports
   milestones, and answers "what's the status of…".
2. **Thread view + approvals:** I can watch and steer a worker live, and approve escalations from a push.
3. **Library + memory:** artifacts are collected per task, and memory is editable in the app.
4. **Avatar + digest:** the living giraffe works, and the daily digests arrive.

## Explicitly not in v1

- Voice.
- Family users and agents.
- Several agents for the owner.
- Telegram.
- Android.
- Claude routines as a scheduling backend. The hub scheduler covers B-22; routines may be added later.
- Harry wiring beyond adding it as an MCP connector.
- Tracking sessions that were not handed over.
