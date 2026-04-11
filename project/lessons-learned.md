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

---

## ISSUE-043: Browser/Web Interaction Skill (2026-04-10)

### What worked well
- Following the exact `skills/tool.py` MCP server pattern made integration seamless — the browser tools plugged into `sessions.py` with just 4 lines of changes
- Making playwright an optional dependency with `is_available()` gate means Marcel works fine without it — graceful degradation by default
- The `_mock_page` factory pattern using `SimpleNamespace` + `AsyncMock` kept test code clean and avoided N801 lint issues from inline mock classes

### What to do differently
- The `TYPE_CHECKING` guard for optional playwright imports still triggered pyright `reportMissingImports` — needed `# pyright: ignore` comments. Future optional deps should be added to pyright's exclude list in `pyproject.toml` instead
- Should have added `packages` requirement type to the skill loader earlier (as its own small issue) — it's a general-purpose feature, not browser-specific

### Patterns to reuse
- In-process MCP server pattern for tools that need rich schemas or image content: `create_sdk_mcp_server` + `tool()` closures over session state
- Per-session resource management: create lazily in `get_or_create`, clean up in `_disconnect_session` — follows the `BrowserContext` lifecycle pattern
- Accessibility tree snapshot with integer refs for LLM interaction — compact, structured, and gives the model a way to target elements without CSS selectors
- SSRF protection module (`is_url_allowed`) with hostname resolution + private IP range checks — reusable for any tool that accepts URLs

---

## ISSUE-051: Continuous Conversation Model (2026-04-10)

### What worked well
- Researching ClawCode and OpenClaw first gave concrete inspiration — ClawCode's microcompaction (selective tool result stripping) and OpenClaw's staged summarization directly shaped the design
- The segment-based storage architecture cleanly separates concerns: active segment (append-only), sealed segments (immutable + summary), search index (append-only). Each file has a clear lifecycle
- Rolling summary chain ("each summary absorbs predecessor") is a simple mechanism that mimics human memory — recent things vivid, old things faded — with no complex data structures
- Aggressive tool lifecycle (2 turns instead of 8) was the single biggest token savings and trivial to implement — just changing two constants and adjusting the trimming function

### What to do differently
- The old `compactor.py` and session management functions in `history.py` were left as dead code rather than deleted — should have removed them in the same commit or created a follow-up cleanup task. Dead code accumulates confusion
- The CLI history loading was requested as a follow-up mid-issue — would have been cleaner as its own subtask from the start. Adding REST endpoints (/api/history, /api/forget) late in the process felt bolted-on
- Route naming started as `/v2/history` then was renamed to `/api/history` in a polish commit — should have picked the final name upfront

### Patterns to reuse
- Segment-based append-only storage with seal+summarize lifecycle — applicable to any system that needs bounded growth with long-term recall
- Keyword search index as a separate append-only JSONL — cheap to build, no external dependencies, good enough for "remember when we talked about X?" queries
- Circuit breaker pattern for background operations (max N consecutive failures) — prevents infinite retry loops on persistent errors
- REST endpoints for CLI state operations (/api/history, /api/forget) — lets the CLI be stateless while the server manages conversation lifecycle

---

## ISSUE-049: Full Migration to v2 Pydantic-AI Harness (2026-04-10)

### What worked well
- The migration was straightforward because v2 was already the primary path — Telegram and the WebSocket endpoint both used `stream_turn()`. The "migration" was mostly deleting v1 code
- Rewriting `memory_extract.py` from `claude_agent_sdk.query()` to a pydantic-ai Agent that returns JSON operations was a clean pattern — eliminates the dependency while keeping the same behavior
- Adding the health check log filter and suppressing httpx/httpcore noise immediately made Docker logs usable — small effort, high value

### What to do differently
- The `/v2/` prefix on endpoints should have been renamed to `/api/` when the endpoints were first created, not as a post-migration cleanup. Endpoint names should reflect purpose, not implementation version
- Multiple other issues' uncommitted changes were in the working tree during this migration — a cleaner approach would be to commit or stash other work first. The pre-commit hook caught test failures from these stale changes, costing debugging time
- The closing commit accidentally picked up `.marcel/` skill file deletions from another issue's work — should have been more careful with `git add` scope

### Patterns to reuse
- For SDK migrations: make the new path the default first (keep old code), then delete the old code in a separate issue — "migrate then delete" is less risky than "rewrite in place"
- JSON-return-value pattern for agent sub-tasks: instead of giving an agent file I/O tools, have it return structured JSON and apply the operations in the caller. Simpler, more testable, no permission issues
- Custom `logging.Filter` subclass on specific loggers (e.g. `uvicorn.access`) to suppress noisy patterns — cleaner than adjusting log levels which affects all messages

---

## ISSUE-059: Clean Up User Data Directory (2026-04-11)

### What worked well
- Writing a standalone migration script (`scripts/migrate_059_cleanup.py`) with `--dry-run` made it safe to verify the migration plan before executing — caught the permission error on root-owned files before it could corrupt data
- Removing the legacy session storage functions entirely (not just deprecating) forced all callers to migrate in the same commit — no half-migrated state
- Consolidating 22 memory files → 10 by merging duplicates and removing derivable/stale content made the memory system much cleaner for the AI selector

### What to do differently
- Root-owned files in `conversations.archived/` from an earlier Docker permission issue weren't discovered until the migration script hit a `PermissionError` — should have checked file ownership during the investigation phase
- The `scripts/` directory is gitignored, so the migration script isn't tracked. For one-shot migrations this is fine, but worth noting that scripts there are disposable

### Patterns to reuse
- When removing a module's public API: grep all imports, update all callers and tests first, then delete the functions in a single commit — ensures no dead import errors
- For data migrations: backup first, dry-run, then execute. The `shutil.copytree` with `copy_function=_copy_ignore_errors` pattern handles permission issues gracefully
- Memory file cleanup criteria: (1) derivable from codebase → delete, (2) ephemeral/stale data → delete, (3) duplicate content → merge into one file with frontmatter, (4) missing frontmatter → add it

---

## ISSUE-061: Harden Job Scheduler (2026-04-11)

### What worked well
- Deep-exploring a reference codebase (OpenClaw) before designing gave concrete, battle-tested patterns to adopt rather than inventing from scratch — the plan practically wrote itself
- All schema changes were additive with defaults, so existing job.json files deserialize without migration — zero backward compatibility risk
- Implementing all 12 features in a single pass was efficient because they share state (e.g. `consecutive_errors` is used by both backoff and alert cooldown)

### What to do differently
- The `_notify_if_needed` refactor changed the return type from `None` to `tuple[str, str | None]` and added a second `append_run` call in `execute_job_with_retries` for delivery tracking — this means each run gets two lines in `runs.jsonl` (one from `execute_job`, one from `execute_job_with_retries`). Should have moved `append_run` entirely to `execute_job_with_retries` instead of appending twice. Worth a follow-up fix.
- No integration test for the full timeout path (would need a mock agent that hangs) — the unit tests cover the model and classification, but end-to-end timeout is untested

### Patterns to reuse
- `classify_error()` with compiled regex patterns for transient detection — simple, fast, extensible. Add new patterns to `_TRANSIENT_PATTERNS` list as new error shapes appear in production
- `_stagger_offset(job_id)` using SHA-256 hash mod window — deterministic, no state, avoids thundering herd. Applicable anywhere multiple items share a schedule
- `_resolve_stuck_runs()` on startup — scan for orphaned RUNNING records and append corrected FAILED records. Good pattern for any append-only log that can be interrupted mid-write
- `asyncio.Semaphore` for bounding concurrent dispatches — one line of state, wraps the execution block cleanly, no complex queuing needed
