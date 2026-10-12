# Marcel v3 — Developer Guide

Marcel v3 is one continuous conversation on the owner's iPhone with an agent on the NUC. The agent starts and steers
Claude Code sessions to do the work, files them in projects, keeps living artifacts up to date, and a pixel-art
giraffe shows what it is doing. It is a shell around the unmodified `claude` CLI on a Claude Max subscription.

**The plan is the source of truth:** [plan/README.md](plan/README.md) → functional spec, architecture, spikes,
[agent playbook](plan/04-agent-playbook.md), and every story in [plan/features/](plan/features/).
Until S-01.1 runs, the v2 code (`src/`, `docs/`, `scripts/`) is still in the tree — do not extend it.

## Core principles

Refer to one by its handle ("Core principle: Recoverable"); reviewers cite them.

- **Lightweight** *(over bloated).* No dependency without need; every part removable.
- **Generic** *(over specific).* A general extension point beats a one-off.
- **Human-readable** *(over clever).* Messages, errors and logs are read on a phone by family members. Lead with
  the outcome, in plain words.
- **Recoverable** *(over fast).* Self-changes go through a PR and the redeploy flag, which rolls back on a failed
  health check.
- **Claude Code does the thinking.** Marcel's own code never calls a model; it starts `claude` sessions.

## How work is tracked

- **Definitions in git:** stories in `plan/features/`, spikes in `plan/03-spikes.md`, decisions in
  `project/decisions/`, lessons in `project/lessons/`.
- **Status on the build board:** https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh — never in a tracked file.
  See [.claude/rules/board.md](.claude/rules/board.md).

| Skill | Who | Does |
|---|---|---|
| `/story [S-…]` | implementer | Claim the next ready story for this machine, branch `story/S-…`, load it |
| `/finish-story` | implementer | Gate, fresh verifier, commit by name, PR, card → review |
| `/merge-story S-…` | lead | Merge in wave order, card → done, report what opened up |
| `/board [status\|sync\|set]` | anyone | Progress summary; copy plan changes to the board; move a card by hand |
| `/adr <decision>` | anyone | Record a decision in `project/decisions/` |

## Lanes

| Lane | Path | Runs on |
|---|---|---|
| contracts | `contracts/` | cloud (lead only) |
| hub | `hub/` | cloud |
| runner | `runner/` | cloud; NUC for `[NUC]` stories |
| plugin | `plugins/marcel/` | cloud; NUC for `[NUC]` stories |
| brain | `brain/` | NUC |
| ios / avatar | `ios/`, `shared/avatar/` | Mac (Xcode) |
| deploy | `deploy/` | NUC |

A story's `runs-on:` decides the machine. At most six implementers at once.

## Commands  (created by S-01.2)

```bash
make check            # every lane's gate except iOS: ruff, pyright, pytest (coverage ≥ 90 %), npm run check
make -C hub check     # one lane
make ios-check        # xcodebuild test (Mac only)
make fake-claude      # the fake claude shim tests use
python3 -m unittest discover .claude/board   # board helper tests
python3 -m unittest discover .claude/hooks   # edit-guard tests
```

## Rules  (auto-loaded from [.claude/rules/](.claude/rules/))

`board` · `contracts` · `no-model-calls` · `inert-controls` · `self-modification` · `docs-in-impl` ·
`debugging` · `git-staging`. A guard hook ([.claude/hooks/guard-restricted.py](.claude/hooks/guard-restricted.py))
blocks edits to `CLAUDE.md`, `contracts/` and env files unless `.claude/.unlock-safety` exists; it prints the
unlock steps.

## Reviewers  ([.claude/agents/](.claude/agents/))

`plan-verifier` (stories are buildable) · `pre-close-verifier` (a story is really done) · `code-reviewer` ·
`security-auditor`. Delegate file-heavy checking to them.
