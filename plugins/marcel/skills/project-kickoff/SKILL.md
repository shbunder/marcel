---
name: project-kickoff
description: How a coding project starts well - the one Kickoff quest that adds what a repo lacks for Claude Code (a CLAUDE.md naming the check command, that command, CI and a PR template), in one PR, never overwriting a file. Use as the brain when a project with a repo is created or a repo's first coding quest runs, and as the worker of a Kickoff quest.
---

# Project kickoff

> **Coding projects start well (B-45).** A new coding project gets a kickoff quest that adds what a Claude Code session
> needs and the repo lacks: a `CLAUDE.md` naming the check command, that command, CI and a PR template.

## For the brain: when to start a Kickoff

- A project with a `repo` was just created (by you with `projects_create`, or by the owner), or the
  first coding quest is about to run in a repo no Kickoff has looked at yet.
- Start **one** quest, before or alongside the owner's own request, never instead of it:
  `tasks_spawn` with `title: "Kickoff <repo name>"`, `location: nuc` (only the NUC's clone has this
  plugin, so a cloud session would not find this skill or its script), the `repo`, the project's
  slug, `expected_minutes: 20`, `kickoff: true` (only a kickoff quest may set the project's
  tracker; any other quest's tracker is refused), and a prompt that says: "Follow the
  project-kickoff skill for this repo."
- Say it in the same line as the rest: "Made *Garden planner* and started *Kickoff garden-planner* on
  the NUC: it adds what the repo lacks for Claude Code."
- Do not start a second Kickoff for the same repo. `tasks_list` with the project shows whether one ran.
- When it is done, copy the check command from its summary under **How to check the work** in the
  project notes (`memory_read` then `memory_write` of `projects/<slug>.md`, with its etag).

## For the worker: the Kickoff quest

1. `report(started)`.
2. Run the check from this skill's folder, in the repo:
   `python3 <this skill's folder>/scripts/missing.py .`
   It prints `{"missing": [...], "check_command": "make check" | null}`.
3. Nothing missing → skip to step 7 (the tracker), then `report(done)` with the summary "Nothing to add: the repo already has a
   CLAUDE.md naming `<command>`, the command, CI and a PR template." No branch, no PR.
4. Otherwise, on a branch `kickoff`, add only what is missing:
   - `check_command`: the lightest check that fits the repo: a Makefile `check` target that runs its
     linter and tests, or a package.json `check` script. Use the tools the repo already uses.
   - `claude_md`: a short `CLAUDE.md`: what the repo is, how to run it, and "Run `<check command>`
     before every commit." If one exists but lacks the command, add that one line; change nothing else.
   - `ci`: `.github/workflows/check.yml` that runs the check command on pushes and PRs.
   - `pr_template`: `.github/pull_request_template.md`: what changed, why, how it was checked.
5. **Never overwrite an existing file**, other than adding the one line to an existing `CLAUDE.md`.
6. Run the check command yourself; it must pass. Open one PR titled "Kickoff: what Claude Code needs
   in this repo", attach it with `report(artifact)`.
7. **The project's tracker** (B-46), when the briefing names no tracker yet: publish
   `templates/tracker/tracker.html` from this plugin (two folders up from this skill) as a new
   claude.ai artifact with the `db` capability (the `Artifact` tool; keep the file in your own job
   folder), titled "<project name> tracker". With `ArtifactData`, `set` the doc `meta/project` to
   `{name, repo, goal}`, then report it:
   `report(artifact, {kind: "link", title: "<project name> tracker", url: "<its
   https://claude.ai/artifact/<id> link>", role: "tracker"})`; any other url is refused. Only the channel carries `role`, which is why a Kickoff runs on the NUC.
   Add this quest's own card (see the worker-protocol skill, "The project's tracker").
8. `report(note)` with "How to check the work: `<check command>`", then `report(done)` with a summary
   that names the check command and lists what the PR adds.
