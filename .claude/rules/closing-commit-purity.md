# Rule — cross-repo commit separation

Work spans two repos: **code** lives in `marcel`, the **board** (tickets, status, requirements,
ADRs) lives in `marcel-admin`. The two commit streams **never mix**.

- **Code commits** (in `marcel`, on `feat/FEAT-…-slug`, prefixed `[STORY-…]`) contain source and
  SDK-doc (`docs/`) changes — **nothing from the board**. No ticket files, no status flips, no
  requirements/ADR edits.
- **Board commits** (in `marcel-admin`, on `main`, prefixed `FEAT-…:` / `STORY-…:` / `ADR-…:`)
  contain ticket/status/requirements/ADR/notes — **no source code**.

There is **no code-side "close commit"**. A feature is marked done by flipping its status on the
board (`jira set FEAT-… status Done`), committed in `marcel-admin`. That board status commit **is**
the audit marker that the feature shipped.

## If you discover something missing right before merge

Commit it in the repo it belongs to. Missing code → a final `[STORY-…] impl:` commit in `marcel`.
Missing requirement/decision/status → a board commit in `marcel-admin`. Then merge the feature
branch. The two histories stay readable independently:

```
marcel (code):        [STORY-…] impl: first chunk → … → merge feat/FEAT-…-slug (FEAT-…)
marcel-admin (board): FEAT-…: create … → STORY-…: … → FEAT-…: done — shipped
```

## Why

Keeping the streams separate keeps both histories useful: `git blame`/`bisect` on the code repo
point at real code changes, never at a status flip; and the board's history reads as a clean record
of what was tracked and when, never polluted by source diffs. Mixing them muddies both.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "The status flip is one line, I'll fold it into the code merge" | It lives in a different repo. There is nothing to fold — `jira set` commits in `marcel-admin`. |
| "I'll just `git add` the board file from the code repo" | The board is a separate working tree; you can't. Run the CLI / `git -C "$MARCEL_ADMIN_DIR"`. |
| "A quick requirements tweak alongside the code is fine" | Requirements are board working docs. They ship as a board commit, not inside a `[STORY-…]` code commit. |

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) flags any code commit on a
feature branch whose diff touches board files, or any board commit that carries source. The
`/finish-feature` skill keeps the merge (code) and the status flip (board) as separate commits in
separate repos.
