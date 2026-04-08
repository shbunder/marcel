# Lessons Learned

Lessons captured after completed issues. Referenced at the start of new feature work to avoid repeating past mistakes and reuse proven patterns.

---

## ISSUE-023: Redesign Skill System (2026-04-02)

### What worked well
- `@register` decorator pattern made integrations pluggable without touching core dispatch code
- Merging chat/coder into a single mode simplified the agent loop — the artificial split was unnecessary
- SKILL.md docs colocated with integration code keep agent instructions in sync with implementation

### What to do differently
- Skill doc symlinks need `.gitignore` entries — easy to forget, causes noisy git status
- Should have migrated iCloud first as a small test case before designing the full framework
- Breaking a 10-subtask issue into smaller issues would have made the git history cleaner

### Patterns to reuse
- `@register("name.action")` decorator for extensibility points
- Symlink pattern: source docs in `src/`, symlinked into `.claude/skills/` via `make install-skills`
- Single-tool dispatch (`integration`) with skill routing — avoids tool proliferation

---

## ISSUE-016: Clean Commit Workflow SOP (2026-03-28)

### What worked well
- The 3-emoji pattern (📝→🔧→✅) makes `git log --oneline` instantly scannable
- Separating the closing commit from code ensures code review happens on implementation commits

### What to do differently
- Should have established this convention from ISSUE-001 — retroactive cleanup is painful
- The "first impl commit moves to wip" rule avoids an empty "I started" commit — non-obvious but important

### Patterns to reuse
- Standalone decision commits (📝) create clear audit trail of "we decided to do this"
- Post-close fixup emoji (🩹) prevents reopening issues for trivial corrections
