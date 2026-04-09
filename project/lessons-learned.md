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
- Skill docs in `.marcel/skills/` with SKILL.md + SETUP.md fallback pattern, loaded from project and home dirs
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

---

## ISSUE-035: Upgrade claude_code to stream-json session (2026-04-09)

### What worked well
- Researching the live CLI (`claude --help`, `claude -p "test" --output-format stream-json --verbose`) before writing code gave exact flag names and event shapes — no guessing
- The `PAUSED:{session_id}:{question}` return-value protocol is simple and self-contained: no shared state, no new dependencies, easy to test
- Using `--resume session_id` for continuation means Claude Code manages its own state; Marcel just passes the ID back

### What to do differently
- Noticed only at reflection time that the `PAUSED:` early return left the subprocess unkilled and un-waited in the `finally` block — would have been caught earlier with a dedicated zombie-process test
- The `assert proc.stdout is not None` works but a type narrowing comment would be cleaner

### Patterns to reuse
- For any subprocess that may exit early via `return` inside a `try`, put `kill()` + `wait_for(proc.wait())` in the `finally` block — never after it — so all exit paths clean up
- Stream-json event loop pattern: `async for raw in proc.stdout` + `json.loads(line)` + dispatch on `event.get('type')` is clean and easy to extend with new event types
- `PAUSED:` prefix protocol: when a tool call needs to pause for user input but can't block, return a structured prefix string the agent can detect and act on, then resume with a follow-up call
