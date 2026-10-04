# Rule — every file in a commit is there because you named it

## Never
- `git add .`, `git add -A`, `git add --all`, `git add -u`, `git commit -a`
- One commit that mixes `project/**` (the board) with source code
- A fast-forward merge of a story branch: the lead merges with `--no-ff`

## Always
- `git status`, then `git add <path> <path> …`, then `git diff --cached --stat` before committing
- Commit format: `[S-NN.M] impl: <what changed>`. Board commits: `board: <what changed>`
- After committing, read `git show --stat HEAD`. A successful commit is not proof of its content

## Why
Broad staging is how a `.env.local`, an APNs `.p8` key, a coverage report or half of another
story ends up in history. Naming files makes every commit a decision.
