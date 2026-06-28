# Rule — docs ship with the change that needs them

Two doc surfaces, two homes — both ship **with** the work, never as an afterthought:

- **SDK / developer reference** (`docs/` mkdocs site, `SKILL.md`, `SETUP.md`, `README.md`, mkdocs
  nav, docstrings that change public API surface) lives in the **code repo** and ships in the
  **last `[STORY-…] impl:` commit** of the feature branch, paired with the final code change.
- **Working docs** (the feature's **requirements page** and **ADRs**) live on the **marcel-admin
  board** and ship as **board commits** while the feature is in flight — written up front
  (`/new-feature`, `/new-adr`) and kept honest as the design settles, before `/finish-feature`.

## Not allowed

- SDK docs deferred to "later" or to a `🩹 fixup` after merge — a fixup is for trivial corrections,
  not half-shipped work.
- A requirements/ADR change smuggled into a `[STORY-…]` code commit — board working docs are board
  commits (see [closing-commit-purity](./closing-commit-purity.md)).
- A feature merged with its requirements page still empty, or an ADR still `Proposed` for a decision
  that was actually made.

## The process

Before `/finish-feature`:

1. **Grep for stale references** to anything you changed — renamed symbols, removed flags, changed
   commit/branch format, new config keys:
   ```bash
   grep -rn "<key term>" docs/ ~/.marcel/ .claude/ README.md SETUP.md mkdocs.yml "$MARCEL_ADMIN_DIR"
   ```
   `~/.marcel/` is the zoo checkout (every skill, integration, channel habitat, `MARCEL.md`,
   `routing.yaml`); `$MARCEL_ADMIN_DIR` is the board. A rename in marcel-core's plugin API almost
   always has echoes in both.
2. **Update every match** — SDK-doc matches in the final code commit; board matches as a board
   commit.
3. **Register new SDK pages in `mkdocs.yml`** under `nav:` — per [docs/CLAUDE.md](../../docs/CLAUDE.md),
   a page missing from nav is invisible and treated as a bug.
4. **Confirm the requirements page reflects what shipped** and every made decision has an `Accepted`
   ADR, **then** finish the feature.

## Why

Missing docs is half-shipped work. Behavior that exists in code but not in the SDK reference confuses
the next reader; a requirement or decision that lives only in someone's head rots by the next
feature. Shipping each doc with the change that needs it is the only way to keep them in sync.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "The docs change is trivial — I'll do it as a fixup" | Fixups are for typos, not docs that were never written. It ships with the final code commit (SDK) or as a board commit (working docs). |
| "No user-visible behavior changed, so nothing to document" | If you renamed a symbol, changed the commit/branch format, or moved a file, docs elsewhere may still describe the old version. Run the straggler grep — let the grep decide, not your memory. |
| "The requirements page can stay a stub, the code is what matters" | The requirements page is the record of what "done" meant. A merged feature with an empty requirements page has lost that record permanently. |

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) runs the straggler grep
against the diff's key terms (across `docs/`, the zoo, and the board) and flags stale references, an
empty requirements page, or a made-but-unrecorded decision.
