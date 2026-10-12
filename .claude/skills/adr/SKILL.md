---
name: adr
description: Record a design decision as an ADR in project/decisions/ — context, options, the decision and its consequences. Use when a story (or the lead) makes a choice that changes a tech choice in plan/02-architecture.md, a contract, or how lanes work together.
---

Decision: $ARGUMENTS

1. Next number: `ls project/decisions/ 2>/dev/null | sort | tail -1` → `ADR-NNN` (start at `ADR-001`).
2. Write `project/decisions/ADR-NNN-<kebab-slug>.md`:

   ```markdown
   # ADR-NNN — <decision as a short sentence>

   Status: Accepted · Date: <YYYY-MM-DD> · Story: <S-… or "lead">

   ## Context
   What forced the choice, in three to six lines. Link the spike or story.

   ## Options
   - **A — …** one line on what it costs and gives.
   - **B — …**

   ## Decision
   The option taken and the one-line reason.

   ## Consequences
   What changes now (files, contracts, stories), and what we give up.
   ```

3. If it changes `plan/02-architecture.md` or a story, update that text in the same commit and run `/board sync`
   afterwards.
4. Commit it on the current branch with the code that depends on it (stage by name):
   `[<story id>] adr: ADR-NNN <slug>` — or, for a lead decision on main, `adr: ADR-NNN <slug>`.

Use `Proposed` only while the owner still has to choose; say so to the owner and ask.
