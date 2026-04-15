# Rule — git staging

Every commit stages files **by name**. Never use broad-add commands.

## Never

- `git add .`
- `git add -A`
- `git add --all`
- `git commit -a`

## Always

```bash
git add path/to/file1.py path/to/file2.py docs/page.md
git commit -m "..."
```

## Why

Broad staging has shipped (in other projects, and narrowly avoided here):

- `.env.local` with API keys — prevented only if the `.gitignore` entry is exactly correct
- Editor scratch files, swap files, coverage reports
- Half-finished work from a different issue the agent forgot was in the tree
- Build artifacts that should have been in `.gitignore` but weren't yet

Named staging is slightly slower but makes every commit **intentional**. Every file in the diff is a file the writer decided to include.

## Exceptions

None. If you catch yourself thinking *"but it's just these three files, `git add .` is fine"*, that is exactly the situation this rule prevents — you will be right ninety-nine times and wrong the hundredth, and the hundredth will be the one that leaks a secret.

Use `git status` first to see the complete picture, then `git add` each file you want by name. The extra ten seconds is cheap insurance.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "I already ran `git status` and the tree is clean — `git add .` is safe here" | "Clean" means clean-to-you. Swap files, coverage artifacts, and a half-finished file from a different issue all show up in `git status`; you will miss one. Name every file. |
| "There are ten files — typing them all is tedious" | Ten files is exactly when broad-add feels justified and exactly when a stray file slips in. If the list is long, paste it from `git status` and delete what doesn't belong. |
| "`git add -u` is fine, it only stages tracked files" | `-u` still sweeps every modified tracked file, including unrelated work in the same tree. The rule is *by name*, not *tracked vs. untracked*. |

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) scans the branch's commits for broad-staging patterns and flags them. The `/new-issue` and `/finish-issue` skills repeat the rule inline for the same reason.
