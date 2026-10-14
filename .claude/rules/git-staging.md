# Rule — every file in a commit is there because you named it

## Never
- `git add .`, `git add -A`, `git add --all`, `git add -u`, `git commit -a`
- One commit that mixes `project/**` or `plan/**` with source code
- A commit that only moves a status: status lives on the build board (see [board](board.md))
- A fast-forward merge of a story branch: the lead merges with `--no-ff`

## Always
- `git status`, then `git add <path> <path> …`, then `git diff --cached --stat` before committing
- Commit format: `[S-NN.M] impl: <what changed>`. Plan and story-definition commits: `plan: <what changed>`
- After committing, read `git show --stat HEAD`. A successful commit is not proof of its content

## Enforced by
`.claude/hooks/guard-git-staging.py` blocks the broad forms above before the shell runs them
(tested in `scripts/tests/test_git_staging_hook.py`).

## Why
Broad staging is how a `.env.local`, an APNs `.p8` key, a coverage report or half of another
story ends up in history. Naming files makes every commit a decision.
