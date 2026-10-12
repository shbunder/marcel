# project/ — what the stories are

One file per story in `stories/` (including the spikes `SP1`–`SP7`) and one per feature in `features/`.
These hold the **definitions** — what a story does, what it depends on, what it touches, when it is done.
The plan's pages in `../plan/` explain the why.

**Status is not here.** It lives on the build board, https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh
(see `.claude/rules/board.md`).

| Command | What it does |
|---|---|
| `/board` | How the build is going: done, in flight, blocked, ready per machine |
| `/story [S-…]` | Claim the next ready story for this machine (or the one named) and branch it |
| `/finish-story`, `/merge-story` | PR and Review; merge and Done (lead) |
| `make board`, `make lanes` | The same view in the terminal, from the latest board export in `.board/export/` |
| `make start S=S-03.2` | Check a story may start: dependencies Done, no overlapping `touches:`, under 6 in flight |
| `/board sync` | After editing `stories/`: bring the board's copies of the definitions up to date |

- `contract-requests/`: an implementer's proposed contract change (see `.claude/rules/contracts.md`).
- `lessons/`: what an agent learned that the next one needs. Read it before starting.
- `decisions/`: ADRs, one per decision that changed the plan (`/adr`).
