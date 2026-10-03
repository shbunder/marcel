# Playbook — how the agent army builds this

The work is split so that many **Sonnet** sessions can implement stories in parallel. One **Opus lead**
session owns the contracts, the merge order and the quality bar. This page is the operating manual for
both roles.

## Roles

| Role | Model | Owns | Never does |
|---|---|---|---|
| **Lead** | Opus | F01, F02 (contracts), spike folding, story refinement, the merge queue, cross-lane integration, the end-to-end acceptance run | Writes large feature code itself |
| **Implementer** | Sonnet | Exactly one story at a time, on its own branch or worktree | Changes `contracts/` (it proposes a change to the lead instead), touches files outside the story's `touches:`, or merges |
| **Verifier** | Sonnet (fresh context) | Runs the pre-merge checklist on a finished story | Fixes things; it reports them |

## Where each lane runs

| Lane | Path | Runs on | Why |
|---|---|---|---|
| contracts | `contracts/` | cloud | Pure text plus schema tests |
| hub | `hub/` | cloud | Pure Python; the runner is faked |
| runner | `runner/` | cloud for logic with the fake `claude`; NUC for integration stories | Needs the real `claude` only for `[NUC]` stories |
| plugin | `plugins/marcel/` | cloud for unit work; NUC for `[NUC]` stories | A channel needs a real session to test end to end |
| brain | `brain/` | NUC | It is a live session |
| ios / avatar | `ios/`, `shared/avatar/` | **Mac** (owner's Mac, through Remote Control) | Needs Xcode, `xcodebuild` and the simulator |
| deploy | `deploy/` | NUC | Real systemd, Docker and cloudflared |

A story's header says `runs-on: cloud | NUC | Mac`. Never start a `Mac` or `NUC` story in a cloud session.

## Waves (the dependency order)

```
Wave 0  (lead + spikes, sequential start)
  F01 repo reset & skeleton ──► F02 contracts + fake runner + mock hub
  SP1…SP7 in parallel (NUC/Mac) ──► lead folds the results into the plan

Wave 1  (fan out — every lane is independent given F02)
  F03 runner core     F04 hub core      F05a channel server   F12a app shell (vs mock hub)
  F13a avatar builder F14a deploy skeleton

Wave 2
  F05b brain tools    F06 brain lifecycle   F07 cloud workers   F08 notifications
  F10 usage guard     F12b thread view      F12c library+memory F13b avatar states

Wave 3
  F09 scheduler+digests   F11 self-modification   F14b cutover (retire old Marcel)
  F15 Harry connector     F16 end-to-end acceptance (lead)
```

A story may start once every story in its `depends:` list is merged to `main`. The lead keeps
`project/lanes.md` up to date; it lists what is in flight and what each story touches.
**At most 6 implementers run at once.** Past that, merge conflicts on shared files cost more than
the parallelism saves.

## The story contract

Every story file in `features/` follows this shape. An implementer must be able to finish using
nothing but the story, the files it links to, and the code.

```
### S-NN.M  Title                                  runs-on: cloud|NUC|Mac · size: S|M|L
depends:  S-…            touches: path/globs …
behaviours: B-…          (from 01-functional-spec.md)
read first: contracts/…, plan/02-architecture.md §…
do:       numbered steps
done when (each one is a test or a command whose output proves it):
  - [ ] …
not in scope: …
```

Size guide:

| Size | Meaning |
|---|---|
| S | Under 300 lines changed, about 1 hour |
| M | Under 800 lines changed, half a day |
| L | Must be split before it is assigned, unless the lead says otherwise |

## The implementer loop

1. `git fetch && git worktree add ../marcel-S-NN.M -b story/S-NN.M origin/main`
2. Read the story, its `read first` files, and the relevant `project/lessons/`.
3. **Write the failing tests first** from the "done when" list, then implement.
4. Run the lane gate:
   - hub, runner, plugin-py: `make -C <lane> check` (ruff, pyright, pytest, coverage ≥ 90 %);
   - plugin-ts: `npm run check`;
   - iOS: `make ios-check` (`xcodebuild test`).
5. Update the docs the story names, in the same commit.
6. Commit and push: `[S-NN.M] impl: <what>`. Stage files by name only; never use `git add .` or `-A`.
7. Hand over to a verifier and fix what it finds. The lead then merges with `--no-ff` in wave order.
8. If you learned something the next agent needs, add `project/lessons/<topic>.md`.

### Hard rules (all lanes)

- **No model calls in Marcel's own code.** Tokens are only ever spent by a `claude` process the runner
  starts. No `anthropic` SDK, no Agent SDK, no API key.
- **Contracts are law.** If a contract is wrong, stop. Open `project/contract-requests/S-NN.M.md` and
  carry on with another story.
- **Tests never reach Claude or the network.** Use the fake `claude` shim, recorded fixtures and
  `respx`. Tests that need the real thing are marked `live`; they are never part of the gate.
- **Every control has a test that fails without it.** This covers the concurrency cap, the usage guard, the approval
  gate, the device-token check and the watchdog. Follow Harry's inert-controls rule: delete the gate and a test
  must go red.
- **Human-readable output.** Milestones, notifications and errors are read on a phone. Write short
  sentences that lead with the outcome.
- **No secrets in git.** Tokens go in `deploy/.env.local` (gitignored) and in the hub's data volume.
- **Restart only via the flag file** (F11). Never `docker restart` or `systemctl restart` from code.

## The verifier checklist (runs before every merge)

- [ ] Every "done when" item is covered by a test or command output that was actually run.
- [ ] Nothing changed outside `touches:`, and `contracts/` is unchanged unless the story is a contract story.
- [ ] The lane gate is green, and coverage did not drop.
- [ ] The degraded path is tested: runner down, hub down, session dead, push failed.
- [ ] Docs are updated, and no TODO points at a board id.
- [ ] Commits are staged by name; none mixes board files and code.

## Board (Harry-style, in-repo)

F01 creates `project/` with:

- `features/`, `stories/` (one file each, generated from these plan files);
- `decisions/` (ADRs);
- `lessons/`;
- `lanes.md`;
- `contract-requests/`.

A story's status comes from git: it is **Done** when its branch is merged with `--no-ff`. There
is no separate status flip.
