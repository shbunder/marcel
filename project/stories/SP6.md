---
id: SP6
title: Hub in Docker ↔ native runner
feature: F00
wave: 0
runs_on: NUC
size: S
status: In Progress
depends: 
touches: scratch/spikes/SP6-*
behaviours: 
---

# SP6 — Hub in Docker ↔ native runner

Blocks: F03, F04, F14

**Steps:**
1. Expose a unix socket from a systemd user service and mount it into a container.
2. Find a UID/GID mapping that lets the container read and write it.
3. Mount `~/.claude/projects` read-only only if the hub needs it; the preferred design is that the runner streams transcripts.
4. Confirm the runner survives `docker compose down` and that its sessions survive a runner restart.

**Pass:** a hello-world round trip, plus a documented compose and systemd snippet.
