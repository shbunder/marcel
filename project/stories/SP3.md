---
id: SP3
title: Cloud sessions from the NUC
feature: F00
wave: 0
runs_on: NUC
size: S
status: Done
depends: 
touches: scratch/spikes/SP3-*
behaviours: 
---

# SP3 — Cloud sessions from the NUC

Blocks: F07

**Steps:**
1. Run `claude --cloud "<task>"` and capture the session id and URL it prints. Try
   `--output-format json` if that is supported.
2. Send a follow-up: `claude -p "<msg>" --cloud <id> --output-format json`. What comes back?
3. Observability. Can a session on the NUC that is connected to Remote Control see the cloud session
   through `ListAgents`, and `SendMessage` to it?
4. Reporting back. Can the cloud session reach `https://marcel-bot.com` (cloud environment network policy)
   and POST a report with a scoped token?
5. Does `claude --teleport <id>` give us the transcript?

**Pass:** a defined way to learn a cloud task's state and get its result, even if that way is
"the worker reports over HTTPS".

## Result

PASS. See [`scratch/spikes/SP3-cloud/RESULT.md`](../../scratch/spikes/SP3-cloud/RESULT.md) and `plan/03-spikes.md` § Results.
