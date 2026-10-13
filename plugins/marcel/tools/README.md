# marcel-tools — the brain's MCP tools

The tools Marcel's brain session uses to act: start, steer, stop and file threads; keep projects; save
artifacts; read and write memory; schedules; push the owner; read plan usage. A small MCP stdio server
(`python3 -m marcel_tools`, standard library only) that turns each tool call into one HTTP call to the
hub's `/brain` routes (`hub/src/marcel_hub/brain_api.py`), written down in
`contracts/brain-api.yaml`.

The plugin starts it for every session (`.claude-plugin/plugin.json`, server `tools`), so Claude Code
names the tools `mcp__plugin_marcel_tools__<tool>`. Only the brain and its side threads get answers;
a worker's call is refused by the hub.

## Tools

| Tool | Hub route | What it does |
|---|---|---|
| `tasks_spawn(title, prompt, location, project?, repo?, model?, expected_minutes?, message_id?)` | `POST /brain/tasks` | Start a thread. `project` is the slug or id it is filed in (`inbox` for the Inbox, also the default). `location` is `nuc` or `cloud`; `auto` is refused with a hint to choose, so the choice shows in the brain's turn. A cloud task needs `repo` (`owner/name`, no leading `-`, no `..`). `message_id` must be a message in the brain's own conversation. |
| `tasks_list(state?, project?, archived?)` | `GET /brain/tasks` | Threads, most recent first (50), each with the slug of its project. |
| `tasks_get(id)` | `GET /brain/tasks/{id}` | One thread (with its project's slug) and its last ten events. |
| `tasks_send(id, text)` | `POST /brain/tasks/{id}/messages` | Steer a thread (`author: marcel`). |
| `tasks_stop(id)` | `POST /brain/tasks/{id}/stop` | Stop a thread. |
| `tasks_move(id, project)` | `POST /brain/tasks/{id}/move` | File a thread under another project; the answer names it by slug. |
| `projects_list(archived?)` | `GET /brain/projects` | Projects with goal, repo and counts. |
| `projects_create(name, …)` | `POST /brain/projects` | A new project and its notes file. |
| `projects_update(project, …)` | `PATCH /brain/projects/{project}` | Change a project. |
| `board_note(project, text)` | `POST /brain/projects/{project}/notes` | A line on the project's board. |
| `artifacts_publish(kind, title, url?, path?, project?, artifact_id?, summary?, in_reply_to?)` | `POST /brain/artifacts` | Save an artifact, or a new version of one. `in_reply_to`: the `digest_id` of the digest it answers. |
| `memory_read(path?)` | `GET /brain/memory` | A memory file with its etag; without `path`, the list. |
| `memory_write(path, content, etag?)` | `PUT /brain/memory` | Save and commit a memory file (`memory: <path> (agent)`). |
| `schedule_create(name, cron, tz, location, prompt, deadline_minutes?)` | `POST /brain/schedules` | Recurring work. |
| `schedule_list()` | `GET /brain/schedules` | Every schedule. |
| `schedule_delete(id)` | `DELETE /brain/schedules/{id}` | Remove one. |
| `notify(text, level?)` | `POST /brain/notify` | Post in the main chat and push it as Marcel's own notice (category `note`; `level` `urgent` or `normal` sets how loud). The hub pushes at most 6 per hour per agent; past that the message is still posted but not pushed (`pushed: false`). Category `broke` is only for the hub's own system faults. |
| `usage_get()` | `GET /brain/usage` | Plan usage and the guard. |

A project is named by its slug or its id. Every tool checks its input (types, lengths, patterns)
before calling the hub and answers a problem in one plain sentence; the hub checks again, and reads
no request body past 64 KB (a memory write: 2 MB).

## Not built yet (the hub answers `not_set_up`)

Each sits behind a small interface on the hub's `app.state.brain_ports` that its story replaces:

- `schedule_*` — the scheduler (S-09.1).
- `usage_get` — only on a hub without a runner. With one, the usage guard (S-10.1) answers; before
  its first reading it says so (`not_set_up`).

## How it is let in

- **Identity:** `CLAUDE_CODE_SESSION_ID` (Claude Code gives it to every plugin server) in
  `X-Marcel-Session`. The hub accepts it only when it is a conversation's `brain_session_id`: the brain
  or a side thread. A worker or adopted session gets 403; anything else 401. A rollover's new brain,
  before its hello, may only read (the hub's `READ_BEFORE_HELLO`), so its hook gets the open threads.
- **Secret:** the hub secret from `$CLAUDE_PLUGIN_DATA/hub-secret` (the channel's) in
  `X-Marcel-Secret`. No device token is accepted, and the secret is accepted nowhere under `/api`.
- **Known limit:** worker sessions on the NUC can read the same `hub-secret` file, so the secret only
  proves "a session on this NUC"; the session-id check is what separates the brain from a worker.
- **Local only:** the routes live under `/brain`, outside `/api`, and the Cloudflare tunnel forwards
  only `/api/*`. A request with Cloudflare's `cf-connecting-ip` header is answered 404 anyway.
- **Its own agent:** every task and project is looked up within the brain's agent; another agent's
  answers 404.
- `MARCEL_HUB_URL` overrides `http://127.0.0.1:7420` for development.

## Development

```bash
make -C plugins/marcel/tools check   # ruff, pyright, pytest with coverage ≥ 90 %
```

The tests drive every tool, as an MCP `tools/call` line, against the real hub app in-process (the
hub is a dev dependency, `[tool.uv.sources]`), with a fake runner. Nothing reaches a network or
`claude`. The server needs Python 3.12 or newer on the host.

The same gate lints the plugin's skills (`tests/test_skills.py`, see `plugins/marcel/skills/README.md`).
`make -C plugins/marcel/tools test-live` runs the brain-routing eval against a real `claude` (NUC only).
