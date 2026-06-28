---
name: new-adr
description: Record an Architecture Decision Record (ADR) under an existing Feature on the marcel-admin board. Use when a feature forces a non-obvious architecture choice worth capturing — the "why it is built this way". ADRs are an independent log; a feature links to 0..n.
---

Record an architecture decision for: $ARGUMENTS

ADRs live in `$MARCEL_ADMIN_DIR/WIKI/decisions/` as an independent global log (`type: adr`). Conventions: `$MARCEL_ADMIN_DIR/WIKI/CLAUDE.md`.

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"
JIRA="$ADMIN/JIRA/jira.py"
```

## Steps

### 1. Identify the parent feature

The decision belongs to a `FEAT-…` (the one whose design it shapes). If ambiguous, `python3 "$JIRA" list features`.

### 2. Allocate + link

```bash
python3 "$JIRA" new-adr FEAT-… "<short decision title>"
```

Allocates `ADR-{YYMMDD}-{hash}`, writes from the MADR template, and appends the id to the feature's `decisions:` list.

### 3. Write the decision

In `$ADMIN/WIKI/decisions/ADR-…-slug.md`, fill the template and set the frontmatter `description:`:
- **Context & problem** — the forces that make a decision necessary now.
- **Decision drivers** — the criteria that matter.
- **Considered options** — at least two, with the rejected ones and why.
- **Decision outcome** — the choice, stated actively.
- **Consequences** — good and bad/trade-offs.

Keep the `## Status` heading and the `status:` frontmatter in sync:

```bash
python3 "$JIRA" set ADR-… status <Proposed|Accepted|Superseded>
```

### 4. Cross-link

Link the ADR from the feature's requirements page (`## Links`) with `[[ADR-…]]`. If it supersedes an earlier decision, set `supersedes:`/`superseded_by:` on both.

### 5. Commit on the marcel-admin board

```bash
git -C "$ADMIN" add WIKI/decisions/ADR-….md JIRA/features/FEAT-*.md WIKI/requirements/FEAT-*.md log.md
git -C "$ADMIN" commit -m "ADR-…: <decision title> (for FEAT-…)"
```

### 6. Report back

The ADR id, its status, and which feature it shapes.
