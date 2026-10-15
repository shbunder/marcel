# Project tracker (S-20.2, B-46)

`tracker.html` is a coding project's board of quests: a claude.ai artifact with a shared database,
readable on the phone and the laptop, so Marcel needs no screen of its own for it. It is generalised
from this repo's build board (`project/board.html`).

**Who writes what**

| Who | When | What |
|---|---|---|
| The project's Kickoff quest (`project-kickoff` skill, on the NUC) | once per coding project | publishes this file with the `db` capability, sets `meta/project`, reports the link with `role: tracker` (the hub sets `Project.tracker_url`) |
| A coding quest (`worker-protocol` skill) | at start, and when its PR is open | its own `cards/<task id>`: In Progress, then Review with `pr_url` and `ci`; one `log` row per move |
| The brain's evening digest turn (`digest` skill) | each evening | cards in Review whose PR merged → Done |

**Data**

- `meta/project`: `{name, repo, goal}`.
- `cards/<task id>`: `{title, quest, status: Backlog|In Progress|Review|Done, pr_url?, ci?: passing|failing|pending, waiting?: bool, updated_at, by}`.
- `log/<id>`: `{at, card, kind: "status"|"note", text, by}`.

The page is read-only: quests and the digest move cards. A card with `waiting: true` shows in the
"Waiting on you" lane until it is Done. Only `https://` PR links are shown. It fixes no width over
400 px; `plugins/marcel/channel/test/tracker-template.test.ts` runs its script against a stand-in
for the browser and checks that.
