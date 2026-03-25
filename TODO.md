# TODO

Outstanding tasks identified during a review of the CLAUDE.md documentation files.

---

## 1. Resolve self-modification safety boundaries

**File:** `project/CLAUDE.md` — Self-Modification Safety section

The safety section contains an unresolved placeholder:

> *"TODO: Define a list of files/modules Marcel is NOT allowed to modify (e.g., auth logic, core config, safety rules). This list should live here once established."*

This is the most critical open item. Until it is resolved, there are no enforced boundaries on what Marcel can rewrite about itself.

**Action:** Decide which files are off-limits for self-modification, write the list into `project/CLAUDE.md`, and remove the TODO. If the decision needs more thought, create a tracked issue and replace the TODO with a reference to it.
