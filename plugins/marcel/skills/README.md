# skills/

Claude Code loads every `<name>/SKILL.md` here for each session the plugin runs in.

| Skill | Read by | What it says |
|---|---|---|
| `orchestrate` | the brain and its side threads | Answer or start a thread, never ask first (B-03), where it runs (B-04), follow-ups to the right thread (B-05), milestones only (B-06), status from `tasks_list` (B-07), model choice (B-13), and which project each thread goes in (B-31): repo, goal or recent threads, else the Inbox; propose a project once when three Inbox threads share a goal; save facts from finished threads to the project notes; add `board_note` lines, never rebuild a board. |
| `worker-protocol` | every worker thread | `report`: `started`, `progress` at milestones with the remaining steps in `next`, `artifact`, and always `done` or `failed` with a summary. A cloud thread posts the same fields to `/api/session/report`. |
| `digest` | the brain | The four digests of B-17 (morning, evening, usage, weekly), written from the hub's facts bundle. |
| `project-kickoff` | the brain, and a Kickoff quest | A new project with a repo (or a repo's first coding quest) gets one Kickoff quest (B-45), on the NUC, where the repo's clone has this plugin. It runs `scripts/missing.py` in the repo and adds, in one PR, only what is missing: a `CLAUDE.md` naming the check command, that command, CI and a PR template. It never overwrites a file, and the check command goes under *How to check the work* in the project notes. |
| `self-improve` | the brain, and a worker in Marcel's own repository | A change to Marcel itself (B-24, ADR-005) is a task on `shbunder/marcel`: branch, test, open a PR, hand it in with `report(artifact)` (the hub flags it and opens the approval card). Never merge it, never restart or redeploy: the owner approves in the app and merges on GitHub, and the hub redeploys. If the owner says no, close the PR with a comment if you can. |

## Checks

`make -C plugins/marcel/tools check` runs `tests/test_skills.py`, which fails when a skill:

- lacks `name` (equal to its folder) or `description` (40–1024 characters, says when to use it);
- is over 200 lines or 12 000 bytes;
- names a tool that does not exist (written as `` `tool(` `` or a bare `` `tasks_…` ``, checked against the brain
  tools and `reply` / `report` in `contracts/channel.schema.json`), or a `report` kind the contract lacks;
- no longer quotes its behaviours word for word from `plan/01-functional-spec.md`.

It also checks the eval cases below against the real tool list, so renaming a tool breaks the gate.

`project-kickoff/scripts/missing.py` is linted, type-checked and tested here too
(`tests/test_kickoff_missing.py`).

The three project cases and the kickoff case also run in the gate, without a model: `tests/fake_brain.py` is a fake `claude -p`
that replays the brain turn recorded for each request (`tests/brain_replays.json`) through the real tools
server to the hub in-process, and `tests/test_project_routing.py` checks each thread lands in the expected
project. The fake refuses to play when the `orchestrate` skill no longer says the rule a turn relies on.

## The live eval (NUC only)

`evals/brain-routing.json` holds ten owner requests and the tool calls the brain should make for each
(start in the cloud, start on the NUC, send a follow-up to the running thread, answer "what's going on?"
from `tasks_list`, honour "use opus", file a thread in a project by its repo, by its goal, or in the
Inbox, and start a Kickoff for a new project with a repo). On the NUC, logged in to the Max plan:

```bash
make -C plugins/marcel/tools test-live
```

Each case runs `claude -p` on Opus with the `orchestrate` skill and a fake brain-tools server that answers
from canned results: no thread is started and the hub is never called. The calls land in
`plugins/marcel/tools/tests/.eval/`. Record the outcome in `project/lessons/brain-skills-eval.md`.
