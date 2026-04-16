# Rule — ✅ close commit purity

A closing commit (`✅ [ISSUE-{hash}] closed: ...`) is a **pure status marker**. It contains exactly:

1. `git mv project/issues/wip/ISSUE-*.md project/issues/closed/ISSUE-*.md`
2. `Status: Closed` change inside the file
3. Task checkbox updates (`[ ]` → `[✓]`, `[⚒]` → `[✓]`)
4. Implementation Log entry with the summary of work
5. Reflection block from the `pre-close-verifier` subagent
6. `## Lessons Learned` section filled in (written into the issue file before the commit)

And **nothing else**. No source code. No docs. No version bumps. No hotfixes. No whitespace cleanup. Nothing.

## If you discover something missing right before close

Commit it as a **final** `🔧 [ISSUE-{hash}] impl: ...` on the feature branch. Then create the close commit. The history should read:

```
🔧 impl: first feature chunk
🔧 impl: second chunk
🔧 impl: final cleanup        ← the thing you just caught
✅ closed: feature shipped     ← pure status marker (this file only)
```

## Why

The close commit is the audit signal that an issue is done. Mixing code into it:

- Muddies the audit trail — `git blame` points at the close commit instead of the real change
- Makes bisect less useful — "what changed" and "when we marked it done" should be two different commits
- Hides pre-close shortcuts — a close commit that also modifies source code bypasses the verifier's "no code in close" sanity check

## If you catch it after the fact

If you already merged an impure close commit, fix it with a `🩹 fixup` commit on main. Do not rewrite history on a merged branch.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "The change is one line, folding it into the close saves a commit" | The close is the audit marker. One line of code in it still breaks `git blame` and still skips the verifier's "no code in close" check. Make the extra `🔧 impl:` commit. |
| "I'll just stage the issue file and trust my editor not to catch the other change" | Per [git-staging](./git-staging.md), staging is by name — so this path only exists if you deliberately typed the other file. Don't. |
| "The pre-close-verifier will let it through, it's only a comment" | The verifier flags any diff outside `project/issues/`. Comments, whitespace, docs — all flagged. The rule is literal. |

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) flags any close commit whose diff touches files outside `project/issues/`. The `/finish-issue` skill's Step 8 repeats the rule.
