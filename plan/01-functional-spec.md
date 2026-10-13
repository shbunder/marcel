# Marcel v3 — functional specification

This is the record of what Marcel v3 must do, written from the interview of 2026-10-03.
Every story on the board (`project/stories/`) traces back to a numbered behaviour here (`B-…`). When a story
and this page disagree, this page wins until the owner changes it.

## In one sentence

Marcel is **one continuous conversation on my iPhone** with an agent that runs on my NUC,
starts and steers Claude Code sessions (on the NUC or in the cloud) to do the actual work,
and keeps me posted — with a living pixel-art giraffe at the top of the screen showing what it is doing.

## Who and what

| Decision | Answer |
|---|---|
| Users | **The owner, then the family** (changed 2026-10-13, B-37…B-40). The owner is the admin. Each family member gets their own helper, running on the owner's Claude login for now. |
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
  Quests tab.
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
  own repo**. The PR shows up as an approval card. Approving it is the go-ahead; the owner then merges the PR on
  GitHub (Marcel never merges itself, ADR-005), and only a PR both approved and merged redeploys through the
  flag-file mechanism, with a health check and an automatic `git revert` rollback.

## The app (iOS only, native Swift)

- **B-25 Tabs** (renamed 2026-10-12 to match the pixel-classic UI; see the wireframes in
  `scratch/spikes/SP5-avatar/wireframes/`):
  - **Chat** (the one conversation with Marcel, with the avatar on top);
  - **Quests** (one card per thread, grouped *waiting on you* (pinned), *working*, *ready for review*, *done*;
    filterable by project) — the Activity tab of earlier drafts;
  - **Map** (projects, B-30);
  - **Items** (the Library, B-21, grouped by project);
  - **Marcel's notebook** (memory, B-20) and **Settings** open from the avatar header.
- **B-26 Thread view.** This is the live transcript described in B-11. It opens from a milestone message, a quest card or a push notification.
- **B-27 Access.** The app reaches the NUC hub through the Cloudflare tunnel (`marcel-bot.com`) and signs in
  with a device token, paired once with a QR code shown by the hub.

## The avatar

- **B-28 Living status indicator.** The **pixel-classic giraffe** (chosen 2026-10-12 after spike SP5; see
  `scratch/spikes/SP5-avatar/RESULT.md`) sits at the top of the chat and in every Marcel message header. It is a
  hand-tuned 48 × 54 sprite played frame by frame, and it animates the agent's state:

  | State | Animation |
  |---|---|
  | idle | breathing, blinking, an ear flick, a glance |
  | thinking | thought dots, eyes up |
  | working(n) | at the laptop, typing; **n spots glow, one per active thread (max 3 shown)** |
  | needs-you | looks at you, a "!" above the head, hearts pulse |
  | done | a nod and a small sparkle |

- **B-29 Made for you.** The sprite comes out of a script (`build.py`): parts, colour ramps and choreography are
  data, so other animals and palettes are new recipes, not new code. v1 ships the giraffe in the Marcel rose palette.
- **B-29a The look** (changed 2026-10-13, ADR-007; replaces the pixel-classic dialogue boxes). A plain chat look,
  see B-43. The pixel art stays in the avatar and in small labels. Body text stays readable at Dynamic Type sizes.

## Projects  (added 2026-10-12)

- **B-30 Projects group the work.** A project is a goal plus, optionally, a repo or folder, standing instructions,
  project notes and its items. Every thread belongs to exactly one project ("Inbox" when nothing fits).
- **B-31 Marcel files, I can move.** Marcel picks the project for each new thread and says it in the "started"
  line. I can move a thread to another project from its card. Marcel creates a project when I ask, or proposes one
  when several threads share a goal.
- **B-32 Still one conversation.** A project room has no chat of its own. Typing there goes to the main
  conversation, tagged with the project, so Marcel's one thread of thought (B-01) stays intact.
- **B-33 Project notes are shared memory.** Each project has a notes file in memory
  (`memory/projects/<slug>.md`). Every thread in the project starts with it, and threads append what later threads
  need to know (as in Claude Code's redesigned Projects).
- **B-33a Claude Code Projects later.** A project can carry a link to a Claude Code Project. In v1 the link is
  informational; when Claude Code Projects can be driven from outside, coding threads of that project may run
  there instead of on the NUC or as single cloud sessions.

## Living artifacts  (added 2026-10-12)

- **B-34 Artifacts are versioned documents.** A thread can publish an artifact and update it in place later
  (v1, v2, v3…). The app shows the latest version, when it changed and by whom, and keeps the history.
- **B-35 Every project has a board.** The board is an artifact the hub keeps current without a brain turn:
  open decisions (waiting on you), threads in flight, recent results with their items, and upcoming schedules.
  Marcel adds the "what this means" lines during its normal turns and digests. Following a project means reading
  its board, not the chat.
- **B-36 Item get.** When a thread finishes with an artifact, the "done" milestone carries a "YOU GOT: <title>!"
  banner and a preview card; tapping opens the artifact. The morning digest (B-17) is itself a board artifact.

## People and sign-in  (added 2026-10-13)

- **B-37 The owner is the admin.** The owner is set up once on the NUC (`marcel-hub setup`) and is the person whose
  Claude login the NUC uses. Only the owner can add or remove people and pair phones for them.
- **B-38 One helper per person.** Each family member gets their own helper (their own name and look), with their own
  chat, quests, projects, notebook and phones. Nobody sees anyone else's. For now every helper runs on the owner's
  Claude login and shares its usage limit and worker cap (ADR-006). Later each adult can bring their own Claude
  account (spike SP8).
- **B-39 Sign-in.** No passwords: a phone is paired once by scanning a code the owner's app (or the NUC) shows.
  Face ID opens the app. "Sign out of this phone" removes that phone's key from the hub.
- **B-40 Settings.** Opened from the avatar: the helper's look and name; notifications and Face ID; the notebook,
  schedules and usage; people (owner only), phones and sign out.

## Chat clarity  (added 2026-10-13)

- **B-41 Answers quote their question.** When an answer, a result or an approval is not for my latest message, it
  shows a small quote of the message it belongs to; tapping it jumps there. Answers to my latest message show no quote.
- **B-42 Mentions.** In the composer, `@` picks a quest or an item and `#` picks a project. Marcel treats an `@quest`
  as "this is about that quest".
- **B-43 The chat look.** Marcel on the left in white or grey bubbles, me on the right in the helper's colour.
  Special messages (started, needs you, done, failed) get a coloured frame all round and a short label. Every tab
  uses the same cards and colours. The Quests filter uses Claude's own session states: waiting on you, ready for
  review, working, done, archived.

## Avatars and coding projects  (added 2026-10-13)

- **B-44 Every helper has its own avatar** from the Pixel Zoo generator: an animal, a colour and a personality,
  picked from the name or by hand, animated for every agent state (ADR-008). Marcel stays the rose giraffe with the
  teal bow tie.
- **B-45 Coding projects start well.** A new coding project gets a kickoff quest that adds what a Claude Code session
  needs and the repo lacks: a `CLAUDE.md` naming the check command, that command, CI and a PR template.
- **B-46 A tracker per coding project.** A Jira-like board (a claude.ai artifact, readable on the phone and the laptop)
  that quests keep up to date: their card, its status and its PR. It is linked from the project room.

## v1 is done when

1. **Chat + spawn + report:** on the iPhone I ask, Marcel starts NUC and cloud threads, reports
   milestones, and answers "what's the status of…".
2. **Thread view + approvals:** I can watch and steer a worker live, and approve escalations from a push.
3. **Items + memory:** artifacts are collected per project and per thread, living artifacts update in place, and
   memory (including project notes) is editable in the app.
4. **Projects:** threads are filed in projects; each project room shows its board, quests and items.
5. **Avatar + digest:** the pixel giraffe reflects Marcel's state, and the daily digests arrive as boards.

## Explicitly not in v1

- Voice.
- Family members on their own Claude accounts (B-38 later, SP8).
- Several agents for the owner.
- Telegram.
- Android.
- Claude routines as a scheduling backend. The hub scheduler covers B-22; routines may be added later.
- Harry wiring beyond adding it as an MCP connector.
- Tracking sessions that were not handed over.
