# hooks/ — the marcel plugin's Claude Code hooks

Claude Code loads `hooks.json` with the plugin, in every session that has it enabled (the brain, side
threads, workers). Each hook is a small standard-library script in `../scripts/`, tested in
`../tools/tests/` (`make -C plugins/marcel/tools check`). A hook never blocks a session: on any problem
it says so in one line, or says nothing, and exits 0.

| Event | Matcher | Runs | What it does |
|---|---|---|---|
| `PreCompact` | (all) | `scripts/brain_context.py` | Just before Claude Code compacts the brain's context (S-06.8, ADR-010), writes `.marcel/handoff.md` in the session's folder: when and why, the open threads (`GET /brain/tasks`), the last message to Marcel and Marcel's last words (read from the end of the transcript). Prints nothing. |
| `SessionStart` | `startup\|resume\|compact` | `scripts/brain_context.py` | Prints what the brain must not lose: after a compaction the hand-over above (once; then it is removed), `MEMORY.md`, the open threads, and today's summary with the live sessions (`GET /brain/summary`, once the hub has it; until then one line says it is missing). |

Only Marcel's brain and its side threads get anything: the brain API refuses any other session (a
worker, an adopted session), and then the hook prints and writes nothing. A brain that has just
started can call before the hub has its id; the hub asks the runner then (`hub/README.md`, "Known
before its spawn returns"), and on start or resume the hook asks again up to 5 times, 1 s apart, with
one line on stderr each time.

Why `type: command` and not `type: http`: the hub secret is a file (`$CLAUDE_PLUGIN_DATA/hub-secret`),
and an http hook can only put environment variables in its headers.

The transcript's JSONL format is Claude Code's own and may change (ADR-010). Reading the last exchange
is best effort: a line it cannot read is skipped, and the test against a recorded transcript
(`contracts/tests/fixtures/transcripts/t1-happy-path.jsonl`) is the alarm.
