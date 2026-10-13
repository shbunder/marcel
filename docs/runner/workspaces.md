# Runner — resume, fork and workspaces

Three things the runner does beyond a plain spawn (`docs/runner/sessions.md`). The fields are in
`contracts/runner-api.yaml`.

| Route | Module | What it does |
|---|---|---|
| `POST /sessions/{id}/resume` | `resume.py` | Wakes a **stopped** session with a new prompt. Answers `200` with the `Session`. |
| `POST /sessions/{id}/fork` | `fork.py` | Starts a new `side` session with the history of another one (SP7). Answers `201` with `Spawned`. |
| `POST /sessions` with `repo` | `workspaces.py` | Clones the repo once, makes a worktree for the task and starts the session there. Answers `201` with `Spawned`, including `branch`. |

`server.route_sessions` sends these three to their modules; every other `/sessions` request goes to
`Sessions.route` as before.

## Resume

A live session is never sent text by the runner. There is no `claude send`; live steering goes
hub → channel. Resume is only for between turns.

1. The session must exist (`404 not_found`) and be stopped. A session that still runs answers
   `409 running`: send the message through its channel.
2. The supervisor is checked (`422 no_supervisor`).
3. A **worker** that wakes up runs again, so it counts toward the worker cap like a spawn
   (`409 cap_reached`). Brain and side sessions never count.
4. The runner waits (up to 10 s) until the session's id has left `~/.claude/daemon/roster.json`.
   SP2 saw a resume ~1 s after `claude stop` start a copy, and the id gone after 3 s. The file's
   format was never recorded, so the runner looks for the full or short id in its text.
5. It runs, in the session's own folder (`--resume` only finds a conversation from there):

   ```
   claude --bg --resume <uuid> --permission-mode <recorded mode>
     --channels plugin:marcel@marcel-local
     --allowedTools mcp__plugin_marcel_marcel__reply mcp__plugin_marcel_marcel__report
     --debug-file <state>/debug/resume-<short-id>-<token>.log -- "<prompt>"
   ```

   `--permission-mode` is the one the runner recorded; it is left out for a session the runner did
   not start, so Claude keeps that session's own. `--channels` is left out when the session was
   spawned with `channel: false`. Brain and side sessions also get the brain tools, by name
   (`sessions.allowed_tools`, the same list spawn uses). The lines are pinned in
   `runner/tests/test_resume_golden/`.
6. If Claude prints `note: … started a copy as <id>` (or backgrounds another short id), the runner
   stops the copy and answers `409 resume_copied`. The message says whether the stop worked.
7. With the channel on, it waits for `Channel notifications registered`, as a spawn does.

## Fork

```
claude --bg --resume <uuid> --fork-session --name <name> [--model <model>]
  --permission-mode <mode> --channels plugin:marcel@marcel-local
  --allowedTools <reply> <report> <every brain tool> --debug-file … -- "<opening prompt>"
```

- The fork's role is `side`, so it never counts toward the worker cap. It works on a live session.
- A **worker is never forked** (`409 not_forkable`): its fork would be an uncounted session with the
  brain tools, which the worker itself never had.
- It takes the permission mode the runner recorded for the session it forks from. A session the
  runner did not start has no record, so its fork runs in `manual`, the strictest mode.
- The model is the request's `model`, else the recorded one. With neither, `--model` is left out.
- It runs in the source session's folder, and keeps its `channel` setting.
- A fork that fails half-way is found and stopped the same way as a spawn. If Claude answers with
  the source's own short id, the runner fails the fork and never stops the source.
- The line is pinned in `runner/tests/test_fork_golden/`.

## Workspaces

| Path | What |
|---|---|
| `~/marcel/workspaces/<repo>` | One clone per repo, made once. `<repo>` is the URL's last part, without `.git`. |
| `~/marcel/workspaces/<repo>-wt/<task-id>` | One worktree per task, on branch `marcel/<task-id>-<slug>`. |
| `~/marcel/workspaces/<repo>-wt/.cloud` | Only while a cloud create on this repo runs `claude --cloud` (see [cloud](cloud.md)). |

`MARCEL_WORKSPACES` moves the root (default `~/marcel/workspaces`).

### The first time a repo is used

1. `git clone -- <url>` into `.<repo>.partial`, renamed to `<repo>` once complete, so a failed clone
   leaves nothing half made.
2. `/.claude/settings.local.json` is added to the clone's `.git/info/exclude` (only this clone).
3. The trust prompt is accepted: an interactive `claude` on a pseudo-terminal. The runner reads the
   screen and presses keys only for the trust dialog: once it shows exactly one highlighted option,
   a "No" option and "Yes, I trust this folder", it moves to Yes with the arrow keys (the default is
   "No, exit", SP2) and presses Enter only when the screen shows Yes highlighted. It presses nothing
   until the screen has been still for half a second: claude 2.1.289 redraws the dialog while it
   starts and loses a key pressed meanwhile. The screen is read as a terminal would show it (claude
   places words with cursor moves and draws the options without numbers). It never answers
   anything else — a repo with `.mcp.json` asks "use these MCP servers?" next — and leaves with
   Ctrl-C. Then `~/.claude.json` must show the folder with `hasTrustDialogAccepted: true` and no
   `enabledMcpjsonServers` or `enableAllProjectMcpServers`. Otherwise the clone fails with
   `422 not_trusted`, the reason in the message and the screen text in `detail`. Worktrees inherit
   trust from the clone.
4. `claude plugin install marcel@marcel-local --scope local`. Local scope writes only
   `.claude/settings.local.json`, so nothing tracked in someone else's repo changes.
5. `git status --porcelain` must be empty. If not, it fails (`422 clone_failed`) and lists what
   changed.
6. `.git/marcel-ready` is written. Until it exists, steps 2–5 run again on the next spawn.

A folder that already holds a clone of another URL is never used (`422 clone_failed`).

### Each task

`git fetch --prune origin`, then `git worktree add -b marcel/<task-id>-<slug> -- <path> origin/<base>`
(`origin/HEAD` without `base`). If the branch already exists it is checked out, never reset. A task
folder that exists on the right branch is used again. If the spawn then fails, the runner removes the
worktree and the branch it just made.

`Workspaces.remove_worktree(spec, delete_branch=False)` removes a task's worktree. It keeps the branch
unless asked, since the work lives there. There is no route for it yet.

### What the runner checks first

Everything in `repo` comes from users. It is checked before git runs (`400 bad_request`):

- `url`: `https://host/path` only. No user or password, no query, no `..` part, no other transport
  (`http`, `file`, `ssh`, `ext::`). It can never start with `-`, and git gets it after `--`.
- The folder name: letters, digits, `.`, `_`, `-`; not starting with `.` or `-`; not ending in
  `-wt` (that name holds another repo's worktrees).
- `task_id`: 1–60 letters, digits, `_` or `-`, not starting with `-`. `slug`: the contract's
  `^[a-z0-9-]{1,40}$`. `base`: a branch name; no `..`, no leading `-`, no trailing `/`, `.` or `.lock`.
- Every path the runner makes or removes must be inside the root with every link followed. A task
  folder that is a link to somewhere else is refused, never deleted.

The rest of the body is checked as an ordinary spawn before any git work. Git runs with
`GIT_TERMINAL_PROMPT=0`, so a private repo fails instead of waiting for a password, and with
`-c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.ext.allow=never` on every call:
workers share the clone's `.git`, and the runner never runs a hook or command one planted there.
Each repo has its own lock, so a slow clone of one repo never holds up another. A cloud create
holds its repo's lock from the clone check to the end of `claude --cloud`, so a task spawn on the
same repo waits for it: usually a fetch and up to 60 s, at worst a first clone and setup plus git
steps of up to 600 s each.

## Errors

| Code | Status | When |
|---|---|---|
| `running` | 409 | Resume of a session that still runs. |
| `resume_copied` | 409 | Claude started a copy; the runner stopped it. |
| `cap_reached` | 409 | Resume of a worker at the cap. |
| `not_forkable` | 409 | Fork of a worker. |
| `bad_request` | 400 | A bad body or `repo`; the message says what. |
| `not_trusted` | 422 | The trust dialog never showed, Yes could not be reached, or `~/.claude.json` does not show the folder trusted (or shows the repo's MCP servers enabled). |
| `clone_failed` | 422 | A git step or the plugin install failed; `detail` is what it said. |
| `git_timeout`, `claude_timeout` | 503 | A git step or the plugin install took too long. Try again later. |
| `no_supervisor`, `exited`, `channel_skipped`, `channel_timeout` | 422 | As for a spawn. |
| `not_found` | 404 | No such session. |
