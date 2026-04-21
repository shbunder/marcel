# Claude Code setup

Marcel's developer-mode harness — the Claude Code session you use when editing Marcel's own source — has a project-local configuration layer under [.claude/](../.claude/). This page explains what lives there, why, and the one workflow you need to know (the safety unlock flag).

!!! note "Runtime subagents are different"
    The `delegate` tool documented in [Subagents](subagents.md) is how Marcel's **runtime agent** splits work at request time. This page is about the **developer session** only — the subagents under `.claude/agents/` run inside Claude Code when you're editing the Marcel codebase, and never see a family member's request.

!!! note "Two repos, two Claude Code sessions"
    Marcel is split across [`marcel`](https://github.com/shbunder/marcel) (the kernel — this repo) and [`marcel-zoo`](https://github.com/shbunder/marcel-zoo) (the habitats). This `.claude/` setup — hooks, rules, subagents, `/new-issue` + `/finish-issue` skills — is scoped to the **kernel** repo. When you're authoring or editing a habitat, open Claude Code in the zoo checkout (`$MARCEL_ZOO_DIR`, default `~/.marcel/zoo`) and follow the zoo's own [README](https://github.com/shbunder/marcel-zoo/blob/main/README.md) for the habitat contract. A kernel change that also touches a habitat should happen as two sessions — one per repo, each with its own issue and commit history.

## Layout

```
.claude/
├── settings.json             # tracked: hooks, statusline
├── settings.local.json       # gitignored: per-machine permission allowlist
├── .unlock-safety            # gitignored: transient safety flag (see below)
├── statusline.sh             # renders the status line
├── hooks/
│   └── guard-restricted.py   # PreToolUse guard on CLAUDE.md, auth, config, .env
├── rules/                    # enforceable rules; see "Rules" below
│   ├── self-modification.md  # always-loaded
│   ├── git-staging.md        # always-loaded
│   ├── closing-commit-purity.md  # always-loaded
│   ├── docs-in-impl.md       # always-loaded
│   ├── debugging.md          # always-loaded
│   ├── integration-pairs.md  # path-scoped: src/marcel_core/skills/, tests/skills/
│   ├── data-boundaries.md    # path-scoped: storage/, auth/, config.py, memory/, channels/
│   └── role-gating.md        # path-scoped: harness/, tools/, agents/
├── skills/
│   ├── new-issue/            # /new-issue procedural wrapper
│   ├── parallel-issue/       # /parallel-issue (worktree-aware)
│   └── finish-issue/         # /finish-issue (delegates to pre-close-verifier)
└── agents/
    ├── pre-close-verifier.md # fresh-context verifier invoked by /finish-issue
    ├── code-reviewer.md      # 5-axis reviewer, Marcel-aware
    └── security-auditor.md   # scoped to Marcel's real attack surface
```

All of `settings.json`, `statusline.sh`, `hooks/`, `rules/`, `skills/`, and `agents/` are tracked — the setup travels with the repo. Only `settings.local.json` and `.unlock-safety` are per-machine.

## Rules

`.claude/rules/*.md` files are enforceable constraints, loaded at the start of every session alongside `.claude/CLAUDE.md`. They complement CLAUDE.md rather than replace it: CLAUDE.md holds workflow prose and architectural context; rules hold short, single-concept, enforceable constraints that are referenced from multiple places (subagents, skills, commit workflow).

### Always-loaded vs path-scoped

A rule file with no frontmatter loads every session. A rule file with YAML frontmatter `paths:` only loads when Claude reads a file matching one of the globs — which saves context on sessions that don't touch that subtree.

```yaml
---
paths:
  - "src/marcel_core/skills/**/*.py"
  - "tests/skills/**/*.py"
---
```

Marcel's five always-loaded rules (`self-modification`, `git-staging`, `closing-commit-purity`, `docs-in-impl`, `debugging`) cover universal workflow safety and debugging discipline. The three path-scoped rules (`integration-pairs`, `data-boundaries`, `role-gating`) cover domain-specific concerns that only matter when touching the relevant code.

### How subagents use rules

The [pre-close-verifier](../.claude/agents/pre-close-verifier.md) enumerates applicable rules at runtime: for each file under `.claude/rules/`, it either reads it unconditionally (no `paths:`) or checks the diff's `git diff --name-only` against the globs. Each rule's `## Enforcement` section names which subagent treats what severity — so a rule can be "machine-read" by the verifier to build its checklist, not just human-read by contributors.

[code-reviewer](../.claude/agents/code-reviewer.md) and [security-auditor](../.claude/agents/security-auditor.md) reference rules by name when flagging violations. Adding a rule automatically extends the verifier's checklist without any skill code change.

### Adding a new rule

1. Create `.claude/rules/<name>.md` with sections: a one-line summary, "Never", "Always", "Why", and "Enforcement" (naming the subagent and severity).
2. If the rule only matters for specific paths, add `paths:` frontmatter with glob patterns.
3. Remove any duplicated prose from CLAUDE.md / GIT_CONVENTIONS / docs that now lives in the rule — link to the rule file instead.
4. If the rule is enforceable by the `pre-close-verifier`, you don't need to edit the verifier — it enumerates `.claude/rules/` dynamically.

## Subagent roster

Subagents run in a fresh context window and report back a single structured result, so their file-reading does not pollute the main conversation.

| Agent | When to use | Returns |
|---|---|---|
| `pre-close-verifier` | Automatically invoked by `/finish-issue` Step 6. Reads the diff and issue, hunts shortcuts and scope drift, greps for stragglers. | APPROVE / REQUEST CHANGES with line references |
| `code-reviewer` | Ask for an independent second opinion on a branch. Aware of pydantic-ai, flat-file storage, the integration pattern, and Marcel's role-gated tool split. | 5-axis review with Critical / Important / Suggestion findings |
| `security-auditor` | Invoke when touching auth, config, credential storage, the restart flag, browser/web fetching, or a new HTTP route. | Findings scoped to Marcel's real threats — not generic OWASP theater |

Invoke them via the `Agent` tool with `subagent_type=<name>`, or — for `pre-close-verifier` — let `/finish-issue` invoke it automatically.

## Hooks

### `PreToolUse` safety guard

Marcel can rewrite its own code. The hook in [.claude/hooks/guard-restricted.py](../.claude/hooks/guard-restricted.py) enforces the "restricted paths" rule automatically. It blocks `Edit`, `Write`, `NotebookEdit`, and `MultiEdit` against any of:

- `CLAUDE.md` (at any depth) — project instructions
- `src/marcel_core/auth/**` — auth module
- `src/marcel_core/config.py` — core config
- `.env*` — environment files

When the hook blocks a tool call, it prints the unlock procedure on stderr and the main context sees it as a tool failure.

### `SessionStart` hint

On every fresh Claude Code session, a short hook prints the active `issue/*` branches (or "No active issue branches") so you can pick up work without running `git branch` yourself.

## The safety unlock flag

When you legitimately need to edit a restricted file (because the user explicitly asked you to, or because you're updating `CLAUDE.md` itself), follow this three-step dance:

```bash
# 1. unlock
touch .claude/.unlock-safety

# 2. make the edit, commit it
$EDITOR CLAUDE.md
git add CLAUDE.md
git commit -m "🔧 [ISSUE-...] impl: ..."

# 3. re-lock — this is not optional
rm .claude/.unlock-safety
```

The status line shows `🔓 unlocked` for as long as the flag is present. The flag is gitignored so it cannot accidentally ship to the repo, but you are still responsible for removing it before the next tool call so the next edit to an unrelated file doesn't silently slip through.

**Rule of thumb:** the unlock flag should exist for as short a time as possible. Set it, edit, commit, delete. If you find yourself leaving it set while doing other work, stop.

## Status line

[.claude/statusline.sh](../.claude/statusline.sh) renders a compact line at the bottom of every Claude Code session:

```
🦒 issue/999fa7-claude-code-setup-hardening • ISSUE-999fa7 • 3✎ • 1 wip • 🔓 unlocked
```

Fields (all optional — omitted when empty):

- Branch (always shown)
- `ISSUE-<hash>` — parsed from the branch name when it matches `issue/<hash>-<slug>`
- `N✎` — uncommitted-file count
- `N wip` — issue files currently under `project/issues/wip/`
- `🔓 unlocked` — the safety flag is present

## Permission allowlist

[.claude/settings.local.json](../.claude/settings.local.json) is per-machine (gitignored) and holds the broad permission allowlist. Keep it small — every entry is a thing the harness runs without asking. The canonical baseline lives in the issue history for `ISSUE-999fa7`; grow it from there only when you notice yourself approving the same command repeatedly.

**Do not add narrow one-shot entries** like `Bash(ls project/issues/closed/ISSUE-070*)` — those were the archaeology that triggered this cleanup. Prefer a broad rule (`Bash(git log:*)`) over twenty specific ones.

## Lessons learned

Each closed issue file contains a `## Lessons Learned` section written at close time (part of the `✅ close` commit). There is no global rotation file — lessons live with the issue that generated them and are searched on demand.

Use `scripts/query_lessons.py` to search across all closed issues at the start of new work:

```bash
# 1–3 keywords from the resolved intent
python scripts/query_lessons.py scheduler timeout
python scripts/query_lessons.py auth webhook --top 5
python scripts/query_lessons.py git staging --since 260101
```

Matches are scored by keyword hit count and sorted by date (most recent first).
