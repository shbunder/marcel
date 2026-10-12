# Rule — docs ship with the change that needs them

Docs ship in the **same commit** as the code that makes them true — never as a follow-up.

- **Developer docs** (`docs/`, a lane's `README.md`, `plugins/marcel/skills/*/SKILL.md`, docstrings on public
  API) go in the story's last `[S-NN.M] impl:` commit.
- **Contracts** (`contracts/`) are docs too: a contract change ships with its tests, by the lead only
  (see [contracts](./contracts.md)).
- **Decisions** go in `project/decisions/` via `/adr`, in the story's branch, when the story made one.
- **Status** never goes in a doc — it lives on the board (see [board](./board.md)).

## Before `/finish-story`

1. **Grep for stale references** to anything you renamed, removed or moved:
   ```bash
   grep -rn "<old name>" docs/ plan/ project/ plugins/ README.md CLAUDE.md .claude/
   ```
2. Update every match in the same commit.
3. If the story's `done when` names a doc, check it exists and says what the code does.

## Not allowed

- "Docs later" or a follow-up PR for docs that were never written.
- A TODO that points at a story id instead of saying what is missing.
- An ADR left `Proposed` for a decision the code already depends on.

## Why

Behaviour that exists in code but not in the docs confuses the next agent, who starts from the docs. A decision
that lives only in a session transcript is gone when the session ends.
