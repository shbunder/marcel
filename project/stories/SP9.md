---
id: SP9
title: Workers publish claude.ai artifacts
feature: F00
wave: 4
runs_on: NUC
size: S
depends: 
touches: scratch/spikes/SP9-worker-artifacts/
behaviours: B-46
---

# SP9 — Workers publish claude.ai artifacts

Blocks: S-20.2 (project tracker).

**Question:** can a background worker on the NUC (`claude --bg`, owner's Max login, no browser) create a claude.ai
artifact with a shared database and update its rows, the way this repo's build board is kept
(`Artifact` publish + `ArtifactData`)? Does it need a one-time approval that an unattended session can't give?

**Steps:**
1. In a background session on the NUC, publish `project/board.html` as a new artifact with the `db` capability.
2. Write and update a few `stories` rows with `if_version`; read them back.
3. Open the link on the iPhone and a laptop.

**Pass:** a worker can publish and update without a human → S-20.2 as written.
**Fallback:** the hub serves the same page itself at `marcel-bot.com/projects/<slug>/tracker` behind the device token
(phone only), and the laptop view waits.
