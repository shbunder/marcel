# Runner — cloud sessions

A cloud session is a Claude Code session on claude.ai/code, not on the NUC. The runner can create
one and queue follow-ups for it. It cannot see its state: `claude agents --json` does not list
cloud sessions, so the worker reports its progress to the hub over HTTPS (SP3). The fields are in
`contracts/runner-api.yaml`; the code is `runner/src/marcel_runner/cloud.py`.

| Route | Runs | Answers |
|---|---|---|
| `POST /cloud/sessions` `{prompt, repo?}` | `claude --cloud -- "<prompt>"` on a pty | `201` `{session_id, url, title}` |
| `POST /cloud/sessions/{session_id}/messages` `{text}` | `claude -p --cloud <id> --output-format json -- "<text>"` | `202` `{ok, session_id, url}`: an ack, not an answer |

Both run with the runner's scrubbed environment (no `CLAUDE*` or `ANTHROPIC_*` variables) and the
runner's `HOME`. The send, and a create without `repo`, run in that home folder. Never steer a cloud session with `SendMessage` from another
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

## Create on a repo

`claude --cloud` takes its repo and branch from the folder it runs in. Without `repo` that is the
runner's home folder, so the session has no repo. With `repo: {url, base?}` (runner-api 0.1.7):

1. The clone `~/marcel/workspaces/<repo>` is made, trusted and given the plugin exactly as for a
   `repo` spawn (see [workspaces](workspaces.md)); this reuses that code.
2. `git fetch --prune origin`.
3. The branch is `base`, or the repo's default branch (`origin/HEAD`) without it.
4. The local branch `<base>` is brought to `origin/<base>` without ever losing a commit:
   - not there yet: made from `origin/<base>`, tracking it;
   - equal to origin: used as it is;
   - behind origin: fast-forwarded (when the clone's own checkout is on it, with
     `git merge --ff-only` there, so the clone stays on its branch and its files follow);
   - ahead of or diverged from origin: refused, `422 clone_failed`, "<base> has commits that were
     never pushed; push them or pick another branch.";
   - checked out in a task's worktree: refused, `422 clone_failed`, naming the task folder.
5. `git worktree add -- ~/marcel/workspaces/<repo>-wt/.cloud <base>` (with `--force` when the clone's
   own checkout is on the same branch: both are then at the same commit).
6. `claude --cloud -- "<prompt>"` runs there.
7. The worktree is removed (`git worktree remove --force`, then `prune`), whether claude succeeded,
   failed or ran out of time.

Why a short-lived worktree: `claude --cloud` exits as soon as claude.ai has the session, and the
cloud box clones the repo from GitHub itself; nothing is pushed from the NUC and the session makes
its own branch. The folder only has to exist while claude reads it. Its name, `.cloud`, can never be
a task id (those never start with a dot). A folder left there by a crash is removed before the next
create. The whole create holds the repo's lock, so two creates on one repo, or a create and a task
spawn, never run git in it at the same time. A task spawn of that repo waits for it: usually a fetch
plus the claude run (at most 60 s), but on a repo's first use a clone, the trust dialog and the
plugin install come first, and each git step may take up to 600 s.

Why a named branch and not a detached checkout at `origin/<base>` (which would touch no branch at
all): `claude --cloud` takes the branch from the folder, and a detached folder has none; what it
then does is unknown. A named branch that is never reset gives it the branch and loses nothing.

## Send

`claude -p … --cloud <id> --output-format json` works without a terminal. It prints
`{"ok":true,"session_id":…,"url":…}`; SP3 saw the cloud session act on it within 5 s. The runner
reads the last JSON line of the output (a warning may come first).

## What the runner checks

- **The prompt and the text** come from users. Each reaches `claude` as one argv entry after `--`,
  never through a shell, so text like `--bg` or `$(…)` is only ever text.
- **`repo`** gets the same checks as a `repo` spawn's, before git or claude runs: `url` is
  `https://host/path` only, with no user or password, no query and no `..` part; `base` is a branch
  name (no leading `-`, no `..`, no trailing `/`, `.` or `.lock`); no other field. Every git call
  disables hooks and fsmonitor, and the `.cloud` folder must be inside the workspaces root with every
  link followed (a link there is removed, never followed).
- **The session id** in the path must match `^session_[A-Za-z0-9]+$` before it goes into argv
  (`400 bad_request` otherwise; claude is not run).
- **The link** is only handed back when it is `https://claude.ai/code/<that session id>`: https,
  host `claude.ai`, no user or port, nothing after the id but a query. The query
  (`?from=cli&m=0`) is dropped. The ack must also name the session the message was sent to.
- **The child** (create and send alike) runs in its own process group. The runner stops reading
  once `claude` has exited and its output has gone quiet, even if a helper it left still holds the
  terminal or pipe open. Then, every time, the whole group is killed, `claude` is reaped and the
  pty or pipe closed, so no zombie, stray helper or open file is left behind.
- **Output size:** at most 1 MiB is read. On create the rest is dropped (the create line comes
  first). On send more than 1 MiB is an error (`cloud_send_failed`): a receipt is one short line.

## Errors

All are `Error` bodies with a `message` a person can act on.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | The body is not `{prompt, repo?}` / `{text}` with a non-empty string, `repo` is not a good `CloudRepo`, or the session id is malformed. |
| 422 | `cloud_unparsed` | Create printed no session id and claude.ai link, or exited with an error. |
| 422 | `claude_version` | A create when the installed Claude Code is not `runner/claude-version`; nothing was started, and the message names both versions. |
| 422 | `clone_failed` | A `repo` create: the clone, its first setup (trust, plugin), the fetch, or checking out `base` failed. Git's or Claude's own words are in `detail`. |
| 422 | `cloud_send_failed` | Send exited with an error, printed no ack, said `ok: false`, acked another session or link, or printed more than 1 MiB. |
| 501 | `not_supported` | A `repo` create on a runner started without workspaces (not expected in production). |
| 503 | `claude_timeout` | Create printed no session within 60 s, or send got no ack within 60 s. The runner killed claude. |
| 503 | `claude_unavailable` | `claude` is missing or cannot be run. |
| 503 | `git_timeout` | A `repo` create: a git step took too long. |

When Claude printed something, the `message` ends with `Claude said: <output>` and `detail` holds
the same output. That output has its terminal escapes and control characters removed, is cut to
2000 characters, and every word of the prompt or text that could be a token (16 or more
token-like characters, such as the task's own report token) is replaced by `[redacted]`.

A create that timed out may still have made the session: the message says to look at
claude.ai/code before trying again.

## Not covered

- **Only proven against the fake claude.** The runner runs `claude --cloud -- "<prompt>"` on a pty
  from `subprocess.Popen`, and `claude -p --cloud <id> --output-format json -- "<text>"`. SP3 ran
  the create *without* `--` and under `pty.fork`. Before F07 relies on cloud tasks, run one real
  create and one real send on the NUC (see `project/lessons/runner-cloud.md` for what to compare).

- **A `repo` create is only proven against a fake.** Whether a real `claude --cloud` started in the
  `.cloud` worktree gives claude.ai the right repo and branch has not been seen yet; see
  `project/lessons/runner-cloud.md` for what to check on the NUC.
- The exact create line comes from one recording (Claude Code 2.1.289). If a newer Claude Code
  changes it, the create answers `cloud_unparsed` with the new output in `detail`.
