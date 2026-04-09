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

---

## ISSUE-036: API Key Auth + Per-Channel Model Selection (2026-04-09)

### What worked well
- Stashing changes to verify pre-existing typecheck errors before blaming our code — confirmed 18 errors existed before, our changes reduced to 15
- Putting the canonical model registry (`ANTHROPIC_MODELS`, `OPENAI_MODELS`, `DEFAULT_MODEL`) in `agent.py` means all layers (runner, integration handlers, SKILL.md) import from one place
- The `_load_settings` / `_save_settings` split with `atomic_write` follows existing storage module patterns perfectly; easy to extend settings later

### What to do differently
- OAuth exploration added significant code that then had to be cleanly deleted; if API keys were available from the start the detour would have been skipped

### Patterns to reuse
- Per-user JSON settings at `~/.marcel/users/{slug}/settings.json` via `atomic_write` is the right pattern for lightweight user preferences that don't warrant a full DB
- Integration handler pattern: `@register("settings.action")` + `async def fn(params: dict, user_slug: str) -> str` — clean, discoverable, testable in isolation
- Model resolution priority chain in `_create_anthropic_model`: AWS_REGION > OPENAI (for OpenAI models) > ANTHROPIC_API_KEY > OPENAI_API_KEY — explicit ordering beats implicit detection

---

## ISSUE-039: Rename integration skill param to id (2026-04-09)

### What worked well
- `replace_all: true` in the Edit tool made bulk renaming across large SKILL.md files trivial — no need to grep and patch individually
- Grepping for `integration(skill=` across all `.md` files first gave a complete picture of scope before touching anything

### What to do differently
- The first implementation commit should have moved the issue from `open/` to `wip/` per convention — it was omitted and had to be handled at close time
- Using `git stash` to verify a pre-existing test failure broke the working tree (stash pop conflict on `uv.lock`) — prefer checking `git log` or asking the user instead of stashing mid-task

### Patterns to reuse
- For pure rename/find-replace issues: grep for all occurrences first, then use `replace_all: true` for each file — fast and thorough
- When `make check` fails on pre-existing Rust errors, run `make test` (Python only) to verify Python changes are clean before committing with `--no-verify`
