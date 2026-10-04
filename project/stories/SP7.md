---
id: SP7
title: Forking the brain for side threads
feature: F00
wave: 0
runs_on: NUC
size: S
status: Done
depends: 
touches: scratch/spikes/SP7-*
behaviours: 
---

# SP7 — Forking the brain for side threads

Blocks: F06 (side threads)

**Steps:**
1. With SP1's channel brain running, fork it into a new background session:
   `claude --bg --resume <id> --fork-session …`, or `/fork` through the brain.
2. Check that the fork has the main thread's context, has its own channel connection (a different
   `MARCEL_CONVERSATION_ID`), and does not write into the original.

**Pass:** two independent background sessions that share history up to the fork.
**Fallback:** start a fresh side session seeded with a hub-generated summary of the main thread.

## Result

PASS. See [`scratch/spikes/SP7-fork-brain/RESULT.md`](../../scratch/spikes/SP7-fork-brain/RESULT.md) and `plan/03-spikes.md` § Results.
