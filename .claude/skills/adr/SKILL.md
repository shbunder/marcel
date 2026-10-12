---
name: adr
description: Record a decision as an ADR in project/decisions/ — context, options, decision, consequences. Use when a story or the lead makes a choice that changes the architecture, a contract, a tech choice or how lanes work.
---

Decision: $ARGUMENTS

1. Next number from `ls project/decisions/` (`ADR-001-…` exists).
2. Write `project/decisions/ADR-NNN-<slug>.md`, in the shape of ADR-001:
   `# ADR-NNN — <the decision as a sentence>`, then `Status: Accepted · Date · Story`, **Context** (what forced
   it, links to the spike or story), **Options** (one line each: cost and gain), **Decision** (and the one-line
   reason), **Consequences** (what changes now, what we give up).
3. If it changes `plan/` or a story, edit those in the same commit, then `/board sync`.
4. Commit on the current branch, staged by name: `[<id>] adr: ADR-NNN <slug>`, or `plan: ADR-NNN <slug>`
   for a lead decision.

`Proposed` only while the owner still has to choose — then ask them.
