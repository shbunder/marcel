# Fake `claude`

A stand-in for the Claude Code CLI, so runner tests never reach Claude or the network. It replays what
the spikes recorded on Claude Code 2.1.289 (SP1, SP2, SP3, SP4, SP7) and writes files where real
`claude` would. Anything it was not taught exits 2 with "is not faked", so a runner that drifts from
the recordings fails loudly.

## Use it in a test

```python
from fake_claude import FakeClaude


def test_something(tmp_path):
    fake = FakeClaude(tmp_path / 'home')  # a scratch HOME it may write into
    fake.use('worker-needs-approval')  # what the next `claude --bg` plays
    # call the code under test with fake.env() (HOME + PATH) or fake.claude (absolute path)
    row = fake.wait_for(short_id, 'blocked')  # polls `claude agents --json --all`
    fake.answer(short_id, 'allow')  # as the channel's permission relay would
    fake.stop_all()  # in teardown
```

Pick the scenario with `fake.use(...)` (written to `~/.claude/fake-claude/scenario`, so it survives a
runner that scrubs the environment) or `FAKE_CLAUDE_SCENARIO`. `use('a', 'b')` makes the first spawn
play `a` and every later one `b`. A scenario is a name from `scenarios/` or a path to a `.yaml` file.

By hand: `make fake-claude SCENARIO=limit-reached` opens a shell with the fake first on `PATH` and a
scratch `HOME`.

## What it fakes

| Command | Output | Recorded in |
|---|---|---|
| `--bg [--name …] … -- "<prompt>"` | `backgrounded · <short-id> · <name>` | SP2 |
| `--bg` in an untrusted workspace | `Workspace not trusted. Run claude in <dir> once and accept the trust prompt`, exit 1 | SP2 |
| `--bg … --debug-file <path>` | `Channel notifications registered` or `… skipped: …` in the debug file | SP1 |
| `--bg --resume <id>` on a live session | a copy, plus `note: session <id> is already running in the background, so this started a copy as <id>` on stderr | SP2 \* |
| `--bg --resume <id>` on a stopped session, no other flag | wakes the same session, which writes on in its saved debug file: `note: woke session <id> with its saved options (…)` on stderr | NUC, 2.1.296 |
| `--bg --resume <id> --<any saved option> …` on a stopped session | a copy, plus `note: background session <id> keeps its own saved options, so the flags you passed started a copy as <id>. …` on stderr | NUC, 2.1.296 |
| `~/.claude/jobs/<id>/state.json` `respawnFlags` | the options a session was started with, in 2.1.296's order (`fixtures/jobs/respawn-2.1.296/`) | NUC, 2.1.296 |
| `--bg --resume <id> --fork-session` | a new session with the full history, no note | SP7 |
| `agents --json [--all]` | one row per session, keys in Claude Code's order; without `--all` a finished session with no process is left out | SP2 |
| `stop <id>` | `stopped <short-id>`; mid-turn → `stopped`, finished → stays `done` | SP2 \* |
| `attach <id>` | nothing (a no-op) | — |
| `--cloud "<prompt>"` | on a terminal: `Created cloud session: … View: … Resume with: …`; otherwise the "requires an interactive terminal" error | SP3 |
| `-p "<msg>" --cloud <id> --output-format json` | `{"ok":true,"session_id":…,"url":…}`; `-p` with `--cloud` and no id → the "cannot be combined with --print" error | SP3 |
| `-p /usage [--output-format json]` | `fixtures/usage/usage-p-text.txt` | SP4 |
| `--version` | `<runner/claude-version> (Claude Code)`; `MARCEL_FAKE_CLAUDE_VERSION` reports another | — |

\* The spikes quote this line only in part. Check the exact words on the NUC before a parser relies on more
than the ids.

Where it writes, under `$HOME/.claude/`:

- `projects/<cwd, every non-alphanumeric character → ->/<session-id>.jsonl`: the transcript, with
  the recorded session id and cwd rewritten to the new ones. Subagents go under `<session-id>/subagents/`.
- `jobs/<short-id>/state.json` and `timeline.jsonl`: the daemon's job files (`needs`, `detail`, `output.result`).
- `fake-claude/`: the fake's own state (sessions, the scenario queue, permission answers, cloud
  sessions, `player.log`). Real Claude Code has no such folder.

It refuses to write into a `~/.claude` that already holds files without its `.fake-claude` marker,
so it never mixes fake sessions into a real Claude Code home.

## Scenarios

| Scenario | What happens | From |
|---|---|---|
| `worker-happy-path` | working → busy → done (subagent, Edit, Bash, commit); `resume` plays the follow-up turn | SP2 t1 |
| `worker-needs-approval` | busy → blocked, `waitingFor: permission prompt`, `needs: approve Bash: …`; held until answered; allow → done, deny → working/idle | SP2 t2, t3 |
| `worker-fails` | busy → `failed`, `needs: API error`, an `isApiErrorMessage` entry (500) | SP4 table, synthesised |
| `limit-reached` | busy → `blocked` without `waitingFor`, `needs: usage limit reached — check plan`, an `isApiErrorMessage` entry (429, "You've hit your limit") | SP4 table, synthesised |
| `workspace-untrusted` | the spawn fails with the trust error | SP2 trap 1 |
| `channel-skipped` | the session runs, but the debug file says the channel was skipped | SP1 |

A scenario file (the shim reads a strict YAML subset: block mappings and lists, scalars, comments; see `miniyaml.py`):

```yaml
description: what this scenario is for
transcript: transcripts/t2-permission-approved.jsonl   # under fixtures/
job: t2-done               # fixtures/jobs/<name>/state.json, the base of the job file
trusted: true              # false → the trust error
channel: registered        # skipped → the debug file says skipped
gate_timeout: 60           # seconds a `gate` waits for an answer before the player gives up
steps:                     # played by `--bg`, one after another
  - wait: 0.1              # seconds after the previous step
    state: blocked         # agents --json `state`; `status` and `waiting_for` go with it
    status: waiting        # leave it out for "no process" (no pid in the row)
    waiting_for: permission prompt
    transcript: 26         # write the transcript up to this line (or `end`)
    subagents: true        # copy the transcript's subagents folder
    text: what the assistant said   # the timeline entry's text
    job:                   # merged into jobs/<id>/state.json; null removes a key
      tempo: blocked
      needs: "approve Bash: touch made-by-t2.txt && ls"
      result: one-line summary      # → output.result
  - gate: permission       # hold until answered: allow carries on, deny plays `denied`
denied: []                 # steps after a deny
resume: []                 # steps after `--resume` (default: busy, then done/idle)
cloud:                     # optional overrides for `--cloud`
  session_id: session_0168oBNCs8Q2BrHDFu6tYUdh
  title: SP3 cloud spike
  output: anything         # replaces the whole "Created cloud session" line
usage: usage/usage-p-text.txt
```

## Fixtures

- `agents/`, `events/`, `jobs/`, `transcripts/t*`: SP2's recordings, moved from
  `scratch/spikes/SP2-worker-control/fixtures/` and sanitised there (`build_fixtures.py`).
- `transcripts/synth-*.jsonl`: SP2 t2's first 26 lines plus one synthesised `isApiErrorMessage`
  entry. A limit hit and an API error were never observed live (SP4); replace them with a recording
  from the first real hit.
- `cloud/`: SP3's evidence. `usage/`: SP4's evidence.

## How it plays

`claude --bg` records the session and starts a detached player (`claude __play <short-id>`) that
walks the steps over time and exits when they are done or a gate gives up. `stop` ends the player.
Scenario timings are fractions of a second, so a whole scenario plays in under a second. The shim is
standard-library Python 3.11+. `bin/claude` switches the network off before the shim runs
(`netguard.py`), so the fake process and its players cannot open a connection; a test plants a
request inside `claude --bg` and checks it is refused.
