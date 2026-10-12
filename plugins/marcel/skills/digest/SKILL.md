---
name: digest
description: How Marcel writes its four digests - morning plan of the day, evening what got done, the usage report and the weekly review. Use when a digest_due system event arrives with its facts bundle, or when the owner asks for a digest.
---

# Digests

The hub wakes you with a `digest_due` system event. Its `data` names the digest (`morning`,
`evening`, `weekly`; the usage report is part of the evening one) and carries the **facts bundle**:
open approvals, threads in flight, done today, PRs ready, failures, usage by task, stale threads and
today's schedules. Write only from those facts. Do not open transcripts or start threads to write a
digest.

The bundle is `data.facts`. Times are already in the owner's time zone; `period` is today (the
weekly review: the last 7 days). Each list is `{count, items, more}`: it keeps the first 10, so
say "and N more" when `more` is not 0. The lists: `approvals`, `questions` (threads waiting for
an answer), `in_flight`, `done`, `failures`, `prs_ready` (checks passing, no changes asked),
`prs_merged`, `artifacts` (new items), `stale` (no activity for 3 days or more) and
`schedules_today`. `usage` is there in the evening digest, the weekly review and a usage report
sent on its own (`data.digest` is then `usage`: write only the Usage section): `plan` (the
five-hour and weekly windows) and `by_task` (each with `points` and `share_pct`).

What each one holds (B-17):

> - **Morning, plan of the day:** pending approvals, today's scheduled jobs, threads in flight,
>   calendar highlights once a calendar connector exists.
> - **Evening, what got done:** finished threads, PRs ready, artifacts, failures.
> - **Usage report:** plan usage consumed, broken down by task.
> - **Weekly review:** a roll-up, plus stale threads to close or archive.

## Rules for every digest

- **One headline line first**: the single thing that matters most today. "2 approvals wait for you."
  or "Quiet day: nothing needs you."
- Then short sections, in the order below. **Leave a section out when it is empty**; never write
  "None".
- Every item is one line: the thread or artifact name in *italics*, then what it means for the owner.
- Things that need the owner come first, always.
- Plain words, real numbers, no jargon. It is read on a phone, sometimes by family.
- Keep it under about 25 lines. More than that: give the count and the top five.

## Where it goes

- Write the digest as Markdown in a file and publish it with `artifacts_publish` (`kind: doc`,
  `path`, and `title` = the event's `data.title`, exactly) as the agent's morning board. The hub
  knows your digest by that title. When the event's `data` gives the board's `artifact_id`,
  pass it, so each digest is a new version of the same board, with `summary` = your headline line.
- Then post one line in the chat with `reply`: the headline, nothing else.

## Morning — plan of the day

```markdown
# Morning, Monday 12 October
**2 approvals wait for you; 3 threads are working.**

## Needs you
- *Fix flaky login test* — approve a push to `main`.

## Today
- 08:00 *Back up the photos* (NUC).
- 18:00 *Water the garden* reminder.

## In flight
- *Docker cleanup* — working on the NUC since 06:40.
```

Sections: **Needs you** (pending approvals, questions) · **Today** (scheduled jobs, with time and
place) · **In flight** (threads working or queued) · **Calendar** (only once a calendar connector
exists).

## Evening — what got done

Sections: **Done** (finished threads, one line of outcome each) · **Ready for you** (PRs ready to
merge, with link) · **New items** (artifacts made today) · **Failed** (what failed and what you
suggest) · **Usage** (below).

## Usage report (part of the evening digest)

- One line on the plan: "Used 62 % of this week's plan; resets Thursday 09:00."
- Then the top tasks by usage, most first, with their share: "- *Redesign Harry cache* — 18 %".
- If usage is near the limit, say what that means: "New work will queue from about 90 %."
- Use the numbers from the bundle; `usage_get` only if the bundle has none.

## Weekly review (Sunday)

Sections: **The week** (a roll-up: threads finished, PRs merged, failures, in numbers) ·
**Highlights** (the three outcomes that mattered most) · **Stale** (threads with no activity for
days, each with a suggestion: close, archive or nudge) · **Usage** (the week's total and the top
three tasks).

Do not close or archive stale threads yourself; list them and let the owner decide.
