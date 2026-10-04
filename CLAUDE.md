# Marcel v3 — working in this repo

Marcel is one continuous conversation on an iPhone with an agent on a home server (the NUC). The
agent starts and steers Claude Code sessions to do the work, and a 3D giraffe shows what it is doing.
**Marcel is a shell around Claude Code**: every model token is spent by the unmodified `claude` CLI on
the owner's subscription. Marcel's code keeps state, moves messages, supervises sessions and draws the app.

Start with [plan/README.md](plan/README.md). What it must do is
[plan/01-functional-spec.md](plan/01-functional-spec.md); how is
[plan/02-architecture.md](plan/02-architecture.md); how work is split is
[plan/04-agent-playbook.md](plan/04-agent-playbook.md).

## Core principles

- **Claude Code thinks, Marcel carries.** No model SDK, no API key, no model call in Marcel's own code.
  Judgement goes to a `claude` session. See [no-model-calls](.claude/rules/no-model-calls.md).
- **Lightweight** *(over bloated).* Every dependency earns its place. Claude Code's own features
  (background sessions, channels, plugins, cloud sessions) come before anything we build.
- **Generic** *(over specific).* A general extension point beats a hard-coded one-off: an agent is
  a record (name, animal, palette, persona), a capability is a plugin or an MCP server, a worker is
  a `claude` session wherever it runs.
- **Human-readable** *(over clever).* Milestones, notifications and errors are read on a phone.
  Lead with the outcome, in short sentences.
- **Recoverable** *(over fast).* Self-changes go through an approved PR, then a redeploy that
  rolls back on a failed health check. See [self-modification](.claude/rules/self-modification.md).
- **Degrading, and loud about it.** One dead session, a down runner or a failed push never takes
  the rest down, and never fails silently.

## Lanes

| Lane | Path | Gate | Runs on |
|---|---|---|---|
| hub | `hub/` | `make -C hub check` | cloud |
| runner | `runner/` | `make -C runner check` | cloud; NUC for `live` tests |
| plugin tools | `plugins/marcel/tools/` | `make -C plugins/marcel/tools check` | cloud |
| plugin channel | `plugins/marcel/channel/` | `npm run check` | cloud; NUC for `live` tests |
| contracts | `contracts/` | contract tests (F02) | cloud, **lead only** |
| brain | `brain/` | template tests | NUC |
| iOS + avatar | `ios/`, `shared/avatar/` | `make ios-check` | **Mac only** |
| deploy | `deploy/` | drills on the NUC | NUC |

## Commands

```bash
make check            # every lane that builds off a Mac, plus the board and hook tests
make lanes            # what is in progress, what it touches, what is ready to start
make start S=S-03.2   # start a story; refused if blocked, overlapping or over the cap
make board            # every story with its status, wave, place and size
make ios-check        # on the Mac only
```

## Working on a story

Follow [plan/04-agent-playbook.md](plan/04-agent-playbook.md) § The implementer loop:

1. `make start`.
2. Write the failing tests from the story's "done when" first.
3. Implement, then run the lane gate.
4. Update docs in the same commit.
5. `[S-NN.M] impl: …`, staged by name.

The rules in [.claude/rules/](.claude/rules/) apply to every change. A guard hook blocks edits to
`CLAUDE.md`, `contracts/**` and env files; its message says how to unlock when the edit is yours to make.
