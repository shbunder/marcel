# ISSUE-037: User Roles System with Admin CLI Capabilities

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Shaun Bundervoet
**Priority:** High
**Labels:** feature, security

## Capture
**Original request:** "let's create roles for users. Shaun should be an admin user. When Shaun uses Marcel, Marcel should be very aware of the server it's running on and be able to perform all typical command line operations. 1) create the roles for users 2) ensure that when admin-users use Marcel, Marcel has broad cli capabilities. Carefully investigate this feature, what is the effects of having broad cli capabilities if Marcel is running in a docker. How can Marcel be aware of the server from inside the docker and take action."

**Follow-up Q&A:**
- User clarified: "I see the docker environment as a shell to run Marcel, but it would be nice that Marcel by default sees the server it is running on (maybe with my homefolder as the current 'working directory') unless we are using CLI, then pwd should be where the CLI is at that point." Marcel currently responded with the container's home (`/home/marcel`) when asked about the home folder, not the host's home (`/home/shbunder`).

**Resolved intent:** Introduce a role system (admin/user) stored per-user in `user.json`. Admin users get unrestricted access to all tools (`bash`, `write_file`, `edit_file`, `git_*`, `claude_code`) plus a server-awareness block injected into their system prompt explaining the Docker environment, host mounts, and Docker socket. Non-admin users get a constrained tool set (memory, notify, integration only) appropriate for a household assistant. Shaun is assigned admin on first run. Because Marcel runs inside Docker with the host filesystem mounted read-only at `/_host` and the Docker socket at `/var/run/docker.sock`, admin users can read host files and manage Docker containers without leaving the container.

## Description

Marcel currently registers all tools unconditionally for every user — including `bash` (arbitrary shell), `write_file`, all `git_*` tools, and `claude_code` (spawns a full Claude Code subprocess). This is appropriate for the developer/admin case but wrong for household users who should only be able to query integrations and memory.

The fix has two parts:

1. **Role storage and propagation** — persist `role` in `users/{slug}/user.json`, load it into `MarcelDeps`, gate tool registration in `create_marcel_agent`.

2. **Admin server context** — for admin users, dynamically detect the runtime environment (Docker vs. bare metal, host hostname via `/_host/etc/hostname`, available mounts) and inject a `## Server Context` block into the system prompt so Marcel understands what it can reach and how.

**Docker realities (investigated):**
- `/_host:ro` — entire host filesystem readable from inside the container
- `/var/run/docker.sock` — Marcel can `docker ps/restart/exec` against sibling containers
- `network_mode: host` — no network isolation; Marcel sees all host ports
- Source is bind-mounted at `/app` (read-write) enabling self-modification
- `$HOME` is bind-mounted (read-write)
- Admin CLI actions that mutate the host (write files outside mounts, run arbitrary host processes) require going through Docker exec or the Docker socket — direct mutation of `/_host` paths is not possible since it's read-only

## Tasks

- [ ] Add `get_user_role(slug) → str` and `set_user_role(slug, role)` to `storage/users.py`, backed by `users/{slug}/user.json`
- [ ] Add `role: str` field to `MarcelDeps` in `harness/context.py`
- [ ] Load role in `harness/runner.py` when building `MarcelDeps`
- [ ] Refactor `create_marcel_agent` in `harness/agent.py` to accept role and conditionally register power tools (`bash`, `write_file`, `edit_file`, `git_*`, `claude_code`) for admin only
- [ ] Add `build_server_context()` helper that detects Docker/bare-metal, reads host hostname, and lists relevant mounts — returns a markdown string for injection
- [ ] Inject `## Server Context` block into the admin system prompt in `harness/context.py`
- [ ] Set Shaun (`shaun`) as admin in `~/.marcel/users/shaun/user.json` (or via setup step)
- [ ] Add `cwd: str | None` to `MarcelDeps`; for admin non-CLI channels default to `$HOME` (host home, bind-mounted at same path); for CLI channel, send current `pwd` from the Rust client
- [ ] Update `bash` tool and file tools in `tools/core.py` to use `ctx.deps.cwd` as working directory instead of hard-coded project root
- [ ] Update Rust CLI (`chat.rs`) to include `cwd` field in `ChatRequest`; populate from `std::env::current_dir()`
- [ ] Update `api/chat_v2.py` to extract `cwd` from message data and pass to `stream_turn`
- [ ] Write tests for role storage round-trip and tool-set gating
- [ ] Document the roles system in `docs/`

## Relationships
- Related to: [[ISSUE-036-api-key-auth-model-selection]] (auth layer — same region of code)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
