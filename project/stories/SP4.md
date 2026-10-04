---
id: SP4
title: Reading plan usage
feature: F00
wave: 0
runs_on: NUC
size: S
status: In Progress
depends: 
touches: scratch/spikes/SP4-*
behaviours: 
---

# SP4 — Reading plan usage

Blocks: F10

**Steps:**
1. Check whether the status-line JSON input carries rate-limit fields (`rate_limits`, five-hour and weekly
   percentages, reset times) on the current version. If it does, a status-line command can write them to a file.
2. Check whether `/usage` has a non-interactive or JSON form.
3. Record the exact limit-reached message and state (`agents --json` → `blocked` / `failed`?), so
   that hitting the limit can be detected.

**Pass:** a reliable source of `used_pct` and `resets_at`, or a documented "detect on hit" fallback.
