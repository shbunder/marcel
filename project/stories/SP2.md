---
id: SP2
title: Worker control from a script
feature: F00
wave: 0
runs_on: NUC
size: S
status: Done
depends: 
touches: scratch/spikes/SP2-*
behaviours: 
---

# SP2 — Worker control from a script

Blocks: F03

**Steps:**
1. Spawn a worker: `claude --bg --name t1 --model sonnet --permission-mode auto "<task>"` in a worktree.
2. Record `claude agents --json --all` across its whole life. Save the fixtures; the fake `claude` shim
   replays them.
3. Find the transcript JSONL for its session id. Tail it, and record one fixture per event type:
   assistant text, tool_use, tool_result, diff, permission, subagent.
4. Stop it (`claude stop`), respawn it, and resume it.
5. Measure the latency from a state change to its appearance in `agents --json`.

**Pass:** a written mapping from `agents --json` and JSONL to `contracts/transcript.schema.json`, plus
fixtures committed in `runner/tests/fixtures/`.

## Result

PASS. See [`scratch/spikes/SP2-worker-control/RESULT.md`](../../scratch/spikes/SP2-worker-control/RESULT.md) and `plan/03-spikes.md` § Results.
