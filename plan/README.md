# Marcel v3 — the plan

Marcel v3 is one continuous conversation on my iPhone with an agent on my NUC. The agent starts and steers
Claude Code sessions to do the work, files them in projects, keeps living artifacts up to date, and a pixel-art
giraffe at the top of the screen shows what it is doing.
It is built as a shell around the unmodified `claude` CLI, on a Claude Max subscription.

| Read | For |
|---|---|
| [01-functional-spec.md](01-functional-spec.md) | **What** it must do: behaviours B-01…B-46, the v1 definition of done, and the non-goals. Decided with the owner on 2026-10-03. |
| [02-architecture.md](02-architecture.md) | **How**: hub, runner, plugin, brain and iOS app; the key flows; the token economy; the data model; the repo layout |
| [03-spikes.md](03-spikes.md) | Phase 0: seven risky unknowns to settle before fan-out |
| [04-agent-playbook.md](04-agent-playbook.md) | How the Opus lead and the Sonnet implementers work: lanes, waves, the story contract, the gates |
| [../project/](../project/) | The board: every feature and story (one file each, sized for one Sonnet session) and the spikes |

## Features at a glance

| Feature | Wave | Lane | Runs on |
|---|---|---|---|
| F01 Repo reset, skeleton, board | 0 | lead | cloud |
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
| F12 iOS app (pixel-classic UI, 10 stories) | 1–2 | ios | **Mac** |
| F13 Pixel avatar (sprite pipeline + player) | 1–2 | avatar | cloud + **Mac** |
| F14 NUC deploy, backups, retire old Marcel | 1, 3 | deploy | NUC |
| F15 Harry as a connector | 3 | plugin | cloud |
| F16 v1 acceptance (+ decision-model measurement) | 3 | lead | NUC + iPhone |
| F17 People, sign-in and settings | 4 | hub / ios | cloud + **Mac** |
| F18 Chat clarity (look, quoted replies, mentions) | 4 | hub / ios | cloud + **Mac** |
| F19 Avatars from the Pixel Zoo | 4 | avatar / hub / ios | cloud + **Mac** |
| F20 Coding projects start well (kickoff, tracker) | 4 | plugin | cloud + NUC (SP9) |

## Before an agent starts

1. The owner reviews `01-functional-spec.md`. It is the contract with the owner.
2. The NUC is upgraded to Claude Code ≥ 2.1.280, and the Mac is reachable through Remote Control.
3. The lead runs F01, then F02, while spikes SP1–SP7 run. The lead updates this plan with the spike
   answers. Only then does wave 1 fan out, with at most 6 implementers at a time.

## Build order for the app and the NUC backend  (updated 2026-10-12)

The app and the backend meet at the contracts, so they are built side by side against fakes, then joined.
Wireframes: `scratch/spikes/SP5-avatar/wireframes/` (12 screens, 6 flows).

| Step | NUC backend (hub, runner, brain) | iOS app | Joined when |
|---|---|---|---|
| 1 · contracts | S-02.1 app API + events ✓, S-02.2 runner + channel protocol, S-02.5 projects, artifact versions, boards and the avatar sheet, S-02.3 fake claude, S-02.4 mock hub with the wireframe scenario | — | the mock hub walks all six flows with curl |
| 2 · skeletons | S-04.1–04.3 hub app, auth, conversations; S-03.x runner on the fake shim | S-12.1 project, MarcelKit, pairing; S-12.2 PixelKit; S-13.1–13.2 sprite pipeline and player | the app pairs with the mock hub and shows the avatar |
| 3 · the work loop | S-04.4–04.6 tasks, runner client, channel gateway; S-05.x plugin | S-12.3 chat, S-12.5 quests, S-12.6 quest view | flows 1–3 run on the mock hub |
| 4 · projects and items | S-04.10 projects, S-04.11 living artifacts and boards, S-04.7 memory | S-12.7 map, S-12.8 items, S-12.9 notebook, S-12.4 side chat | flows 4–6 run on the mock hub |
| 5 · the brain | S-06.1–06.6 brain, rollover, side threads, wake policy, project routing | S-12.10 settings, deep links; S-13.3 avatar in app | the app talks to the real hub on the NUC |
| 6 · platform | F07 cloud workers, F08 push, F09 digests (boards), F10 usage guard | — | F16 acceptance on the iPhone |
