# Rule — docs ship with the last impl commit

Documentation updates (`docs/`, `SETUP.md`, `SKILL.md`, `README.md`, mkdocs nav, inline docstrings that change public API surface) ship in the **last `🔧 impl:` commit of the feature branch**, paired with the final code change.

## Not allowed

- Docs in the `✅ close` commit — forbidden by [closing-commit-purity](./closing-commit-purity.md)
- Docs in a `🩹 fixup` commit after merge — a fixup is for trivial corrections the writer missed, not for half-shipped work
- A separate "docs-only" impl commit at the very end, if it's part of the same feature — bundle it with the final code chunk so the commit represents the feature's "last mile"

## The process

Before creating the close commit:

1. **Grep for stale references** to anything you changed — renamed symbols, removed flags, new emoji, changed commit format, new config keys:
   ```bash
   grep -rn "<key term>" docs/ ~/.marcel/skills/ src/marcel_core/defaults/ .claude/ README.md SETUP.md mkdocs.yml
   ```
2. **Update every match** in the same commit as the final code change.
3. **Register new pages in `mkdocs.yml`** under `nav:` — per [docs/CLAUDE.md](../../docs/CLAUDE.md), a page missing from nav is invisible and treated as a bug.
4. **Only then** create the close commit.

## Why

Missing docs is not a trivial correction — it is half-shipped work. A feature whose behavior exists in code but not in docs will confuse the next reader (human or agent) and will have rotted by the time anyone notices. Shipping docs with the code is the only way to keep them in sync.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "The docs change is trivial — I'll do it as a fixup" | Fixups are for typos the writer missed, not for docs that were never written. Trivial or not, it ships with the final `🔧 impl:` commit. |
| "No user-visible behavior changed, so nothing to document" | If you renamed a symbol, changed a commit format, or moved a file, docs elsewhere may still describe the old version. Run the straggler grep — let the grep decide, not your memory. |
| "`docs/` wasn't touched, so I skipped the grep" | The grep scope is wider than `docs/`: skills, defaults, `.claude/`, README, SETUP, mkdocs. Running it is cheap; skipping it is how stale references survive. |

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) runs the straggler grep against the diff's key terms and flags any match in docs that still describes the old behavior.
