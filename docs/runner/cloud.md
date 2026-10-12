# Runner — cloud sessions

A cloud session is a Claude Code session on claude.ai/code, not on the NUC. The runner can create
one and queue follow-ups for it. It cannot see its state: `claude agents --json` does not list
cloud sessions, so the worker reports its progress to the hub over HTTPS (SP3). The fields are in
`contracts/runner-api.yaml`; the code is `runner/src/marcel_runner/cloud.py`.

| Route | Runs | Answers |
|---|---|---|
| `POST /cloud/sessions` `{prompt}` | `claude --cloud -- "<prompt>"` on a pty | `201` `{session_id, url, title}` |
| `POST /cloud/sessions/{session_id}/messages` `{text}` | `claude -p --cloud <id> --output-format json -- "<text>"` | `202` `{ok, session_id, url}`: an ack, not an answer |

Both run with the runner's scrubbed environment (no `CLAUDE*` or `ANTHROPIC_*` variables) and the
runner's `HOME`, in that home folder. Never steer a cloud session with `SendMessage` from another
session: SP3 saw it accepted and never acted on.

## Create

`claude --cloud` refuses without a terminal ("requires an interactive terminal") and cannot be
combined with `-p`, so the runner gives it a pty. On success Claude Code 2.1.289 printed one line
and exited:

```
Created cloud session: <title> View: https://claude.ai/code/session_<id>?from=cli&m=0 Resume with: claude --teleport session_<id>
```

The runner strips terminal escapes and reads the title, the id and the link from that line. The
cloud box starts working within about 20 s.

## Send

`claude -p … --cloud <id> --output-format json` works without a terminal. It prints
`{"ok":true,"session_id":…,"url":…}`; SP3 saw the cloud session act on it within 5 s. The runner
reads the last JSON line of the output (a warning may come first).

## What the runner checks

- **The prompt and the text** come from users. Each reaches `claude` as one argv entry after `--`,
  never through a shell, so text like `--bg` or `$(…)` is only ever text.
- **The session id** in the path must match `^session_[A-Za-z0-9]+$` before it goes into argv
  (`400 bad_request` otherwise; claude is not run).
- **The link** is only handed back when it is `https://claude.ai/code/<that session id>`: https,
  host `claude.ai`, no user or port, nothing after the id but a query. The query
  (`?from=cli&m=0`) is dropped. The ack must also name the session the message was sent to.
- **The pty child** runs in its own process group. When it runs past the timeout the whole group is
  killed, and the child is always waited for and both pty ends closed, so no zombie or open file
  is left behind. The runner reads at most 1 MB of its output.

## Errors

All are `Error` bodies with a `message` a person can act on.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | The body is not `{prompt}` / `{text}` with a non-empty string, or the session id is malformed. |
| 422 | `cloud_unparsed` | Create printed no session id and claude.ai link, exited with an error, could not run, or ran past 60 s. |
| 422 | `cloud_send_failed` | Send exited with an error, printed no ack, said `ok: false`, acked another session or link, or ran past 60 s. |

When Claude printed something, the `message` ends with `Claude said: <output>` and `detail` holds
the same output. That output has its terminal escapes and control characters removed, is cut to
2000 characters, and every word of the prompt or text that could be a token (16 or more
token-like characters, such as the task's own report token) is replaced by `[redacted]`.

A create that timed out may still have made the session: the message says to look at
claude.ai/code before trying again.

## Not covered

- The request has no `cwd` or repo. The runner creates the session from its home folder; which repo
  a cloud session gets from that was not recorded in SP3 (it was run from the marcel checkout).
- The exact create line comes from one recording (Claude Code 2.1.289). If a newer Claude Code
  changes it, the create answers `cloud_unparsed` with the new output in `detail`.
