# Marcel v3 — the plan

Marcel v3 is one continuous conversation on my iPhone with an agent on my NUC. The agent starts and steers
Claude Code sessions to do the work, and a living 3D giraffe at the top of the screen shows what it is doing.
It is built as a shell around the unmodified `claude` CLI, on a Claude Max subscription.

| Read | For |
|---|---|
| [01-functional-spec.md](01-functional-spec.md) | **What** it must do: behaviours B-01…B-29, the v1 definition of done, and the non-goals. Decided with the owner on 2026-10-03. |
| [02-architecture.md](02-architecture.md) | **How**: hub, runner, plugin, brain and iOS app; the key flows; the token economy; the data model; the repo layout |
| [03-spikes.md](03-spikes.md) | Phase 0: seven risky unknowns to settle before fan-out |
| [04-agent-playbook.md](04-agent-playbook.md) | How the Opus lead and the Sonnet implementers work: lanes, waves, the story contract, the gates |
| [features/](features/) | Every feature and its stories, each sized for one Sonnet session |

## Features at a glance

| Feature | Wave | Lane | Runs on |
|---|---|---|---|
| F01 Repo reset and skeleton | 0 | lead | cloud |
| F02 Contracts, fake `claude`, mock hub | 0 | lead | cloud |
| SP1–SP7 spikes | 0 | lead + owner | NUC / Mac |
| F03 Runner (host process control) | 1 | runner | cloud + NUC |
| F04 Hub core (state, API, channel gateway) | 1 | hub | cloud |
| F05 Claude Code plugin (channel, tools, adopt, skills) | 1–2 | plugin | cloud + NUC |
| F06 Brain lifecycle, side threads, wake policy | 2 | hub / brain | cloud + NUC |
| F07 Cloud workers | 2 | hub | cloud |
| F08 Notifications (ntfy, APNs) | 2 | hub | cloud |
| F09 Scheduler, digests, watchdogs | 3 | hub | cloud + NUC |
| F10 Usage guard | 2 | hub | cloud |
| F11 Self-modification via PR | 3 | hub / deploy | cloud + NUC |
| F12 iOS app | 1–2 | ios | **Mac** |
| F13 Procedural RealityKit avatar | 1–2 | avatar | cloud + **Mac** |
| F14 NUC deploy, backups, retire old Marcel | 1, 3 | deploy | NUC |
| F15 Harry as a connector | 3 | plugin | cloud |
| F16 v1 acceptance (+ decision-model measurement) | 3 | lead | NUC + iPhone |

## Before an agent starts

1. The owner reviews `01-functional-spec.md`. It is the contract with the owner.
2. The NUC is upgraded to Claude Code ≥ 2.1.280, and the Mac is reachable through Remote Control.
3. The lead runs F01, then F02, while spikes SP1–SP7 run. The lead updates this plan with the spike
   answers. Only then does wave 1 fan out, with at most 6 implementers at a time.
