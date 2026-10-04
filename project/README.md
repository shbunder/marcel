# project/ — the board

One file per story in `stories/` (including the spikes `SP1`–`SP7`) and one per feature in
`features/`. The plan's pages in `../plan/` explain the why; these files are what agents pick up.

| Command | What it does |
|---|---|
| `make board` | List every story with status, wave, where it runs and size |
| `make lanes` | What is in progress and what it touches, plus what is ready to start |
| `make start S=S-03.2` | Start a story. Refused if a dependency isn't Done, if its `touches:` overlaps a story in progress, or if 6 stories are already running |
| `python3 scripts/board.py set S-03.2 status Done` | The lead marks a story Done in the board commit that follows its merge |

- `contract-requests/`: an implementer's proposed contract change (see `.claude/rules/contracts.md`).
- `lessons/`: what an agent learned that the next one needs. Read it before starting.
- `decisions/`: ADRs, one per decision that changed the plan.
