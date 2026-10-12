# skills/

Claude Code loads every `<name>/SKILL.md` here for each session the plugin runs in.

| Skill | Read by | What it says |
|---|---|---|
| `orchestrate` | the brain and its side threads | Answer or start a thread, never ask first (B-03), where it runs (B-04), follow-ups to the right thread (B-05), milestones only (B-06), status from `tasks_list` (B-07), model choice (B-13). |
| `worker-protocol` | every worker thread | `report`: `started`, `progress` at milestones with the remaining steps in `next`, `artifact`, and always `done` or `failed` with a summary. A cloud thread posts the same fields to `/api/session/report`. |
| `digest` | the brain | The four digests of B-17 (morning, evening, usage, weekly), written from the hub's facts bundle. |

## Checks

`make -C plugins/marcel/tools check` runs `tests/test_skills.py`, which fails when a skill:

- lacks `name` (equal to its folder) or `description` (40–1024 characters, says when to use it);
- is over 200 lines or 12 000 bytes;
- names a tool that does not exist (written as `` `tool(` `` or a bare `` `tasks_…` ``, checked against the brain
  tools and `reply` / `report` in `contracts/channel.schema.json`), or a `report` kind the contract lacks;
- no longer quotes its behaviours word for word from `plan/01-functional-spec.md`.

It also checks the eval cases below against the real tool list, so renaming a tool breaks the gate.

## The live eval (NUC only)

`evals/brain-routing.json` holds five owner requests and the tool calls the brain should make for each
(start in the cloud, start on the NUC, send a follow-up to the running thread, answer "what's going on?"
from `tasks_list`, honour "use opus"). On the NUC, logged in to the Max plan:

```bash
make -C plugins/marcel/tools test-live
```

Each case runs `claude -p` on Opus with the `orchestrate` skill and a fake brain-tools server that answers
from canned results: no thread is started and the hub is never called. The calls land in
`plugins/marcel/tools/tests/.eval/`. Record the outcome in `project/lessons/brain-skills-eval.md`.
