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
- The `_load_settings` / `_save_settings` split with `atomic_write` follows existing storage module patterns perfectly; easy to extend settings later

### What to do differently
- OAuth exploration added significant code that then had to be cleanly deleted; if API keys were available from the start the detour would have been skipped
- **The `ANTHROPIC_MODELS` / `OPENAI_MODELS` / `_resolve_model_string` registry (added here) was removed in ISSUE-073** — it duplicated provider-selection logic that pydantic-ai already does natively via `provider:model` strings. A curated registry is fine for UX, but it shouldn't double as the dispatch layer.

### Patterns to reuse
- Per-user JSON settings at `~/.marcel/users/{slug}/settings.json` via `atomic_write` is the right pattern for lightweight user preferences that don't warrant a full DB
- Integration handler pattern: `@register("settings.action")` + `async def fn(params: dict, user_slug: str) -> str` — clean, discoverable, testable in isolation

---

## ISSUE-073: Simplify model routing via pydantic-ai native `provider:model` strings (2026-04-13)

### What worked well
- Deleting code beats maintaining it: `_resolve_model_string` + `_BEDROCK_MODEL_MAP` + dual `ANTHROPIC_MODELS` / `OPENAI_MODELS` registries (~60 loc) collapsed to one `KNOWN_MODELS` dict used only for display labels.
- **Self-healing settings migration** in `_load_settings`: detect unqualified legacy values (`no ':' in model`), prepend `anthropic:`, rewrite the file transparently. No migration script, no version flag, no cutover window.
- Shape-only validation (`':' in value`) turns "add a new model" from a code change into a zero-touch config change — any pydantic-ai-supported `provider:model` works immediately.

### What to do differently
- Memory agents (`selector.py`, `extract.py`, `summarizer.py`) were passing **unqualified** model names directly to `Agent()` for months — they only worked because pydantic-ai tolerated the legacy short form. If we'd had a test that instantiated them against a known-strict pydantic-ai version, we'd have caught this earlier. Lesson: mock-free integration-shape tests on model string validity are cheap and catch silent drift.

### Patterns to reuse
- **Trust the framework**: before writing an abstraction layer on top of a library, check whether the library already does what you need. Pydantic-ai's `provider:model` dispatch predated the routing layer we built; we just hadn't used it.
- **Shape validation > whitelist validation** when the whitelist is the thing preventing extensibility. Save the registry for UX, use shape-only checks at the enforcement boundary.

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

---

## ISSUE-062: Restructure User Data Directory (2026-04-11)

### What worked well
- Profile.md frontmatter as a key-value store for small config fields (role, chat_id) avoids single-field JSON files — one file per user instead of three
- Reusing the existing `channel.meta.json` `last_active` field for telegram idle detection eliminated the global `sessions.json` entirely — no new code needed, just removed the old
- The migration script pattern from ISSUE-059 (dry-run first, then execute) was directly reusable here

### What to do differently
- The frontmatter parser strips quotes but doesn't handle all edge cases (e.g., values with colons inside quotes). For now this is fine since all values are simple strings, but if profile.md grows more complex fields, a proper YAML parser might be needed
- Should have checked `uv.lock` changes earlier — the version bump from issue 061 on main caused a diff that was distracting during pre-close verification

### Patterns to reuse
- Profile.md frontmatter for per-user structured config: `_parse_profile()` + `_serialize_profile()` + `_update_profile_field()` — simple, no dependencies, works with any key-value pair
- Delegating session state to an existing metadata store (conversation channel meta) instead of maintaining a separate state file — reduces moving parts and avoids multi-user isolation issues
- `cache/` subdirectory convention for SQLite databases — keeps caches separate from identity/config files, easy to exclude from backups or clear

---

## ISSUE-058: Improve memory system and learning from feedback (2026-04-11)

### What worked well
- Renaming `_human_age` → `human_age` to make it a proper public function was cleaner than importing a private function cross-module
- Adding memory consolidation to the existing `_cleanup_loop` rather than creating a separate module kept the scheduler simple
- The `_format_memory_label` helper produces clean `### [type] name (age)` headers that integrate naturally with the existing `## Memory` section structure

### What to do differently
- The issue had earlier commits (stale issue cleanup, guardrails) that weren't related to the memory improvements — this made the diff noisier during review. Future issues should stay tightly scoped to their stated intent.

### Patterns to reuse
- `_load_job_memories` pattern: loading a subset of memories by type for injection into job agents — avoids full AI-driven selection when there's no user query to match against
- `rebuild_memory_index` as a disk-scan-based index rebuilder — eliminates index drift from background extractors that may crash mid-write
- Structured feedback memory format (rule + **Why:** + **How to apply:**) — gives the agent enough context to judge edge cases rather than blindly following rules

---

## ISSUE-060: Improve Morning Digest Format and Delivery (2026-04-11)

### What worked well
- Tracing the full notification flow end-to-end (agent → tool → executor → Telegram) before writing code revealed a third problem (job notify routing) that would have caused a regression if missed
- Using `deps.notified` as a simple boolean flag kept the double-send fix minimal — no new state machines or event buses needed

### What to do differently
- The job channel prompt said "plain text only" but the Telegram pipeline already had `markdown_to_telegram_html`. Should have questioned this mismatch when the job system was first built — the formatting pipeline exists precisely so agents can write markdown.
- The `_notify` tool routing `channel == 'job'` to Telegram is a bit hardcoded. If jobs ever deliver to other channels, this will need a proper channel lookup from the job definition. Fine for now since all jobs go to Telegram.

### Patterns to reuse
- `deps.notified` flag pattern: lightweight in-run state tracking between tools and executor without modifying the agent loop — useful for any "did the agent already do X?" checks
- `run.agent_notified` on `JobRun`: persisting tool-side state into the run record so post-execution logic can make decisions — avoids passing deps objects through the retry/notify chain

---

## ISSUE-064: Job Scheduler Timezone Support (2026-04-11)

### What worked well
- The fix was minimal: one new field on `TriggerSpec`, a timezone branch in `_compute_next_run`, and job data updates. No schema migration needed thanks to `None` default
- `ZoneInfo` from the stdlib handles DST transitions automatically — no third-party timezone library needed
- Checking the tool layer (create_job/update_job) during reflection caught a gap that would have required a follow-up fix

### What to do differently
- Timezone support should have been considered when the cron scheduler was first built (ISSUE-061). Any system that interprets cron expressions for end users should default to local time, not UTC
- The user's profile already had `Europe/Brussels` — could have used that as a default for new jobs instead of requiring explicit timezone on each job

### Patterns to reuse
- `ZoneInfo` + `astimezone()` for timezone-aware cron: convert UTC `now` to local, run croniter in local time, convert result back to UTC. Simple and handles DST correctly
- Additive schema changes with `None` defaults for backward compatibility — existing job.json files deserialize without migration

---

## ISSUE-065: News Sync Integration (2026-04-11)

### What worked well
- Following the `banking.sync` pattern made the design obvious ��� fetch in code, store in cache, expose single integration call
- Extracting `fetch_feed()` from `rss_fetch()` cleanly separated the reusable library from the agent tool, allowing sync code to import it directly
- Concurrent feed fetching with `asyncio.create_task` keeps sync fast despite 20 feeds
- Feed config in YAML makes it trivial for users to add/remove sources without touching code or job prompts

### What to do differently
- The original `rss_fetch` should never have been an agent tool — it was always doing deterministic work (HTTP + XML parsing) that code handles better. When designing tools, ask: "does this need LLM judgment?" If no, make it a code path, not a tool
- The job system prompt mixed two calling conventions (`rss_fetch(...)` and `integration(id=...)`) which confused the model. System prompts for jobs should use exactly one tool-calling pattern
- Default seeding only copied whole directories, so adding new files to existing skills required manual copying. The fix (seed individual missing files) should have been the original design

### Patterns to reuse
- `news.sync` pattern: YAML config for data sources → async fetch all → deduplicate → filter known → upsert new. Reusable for any periodic data collection integration
- Fall-back config loading: check user data dir first, then bundled defaults. Lets code work out-of-the-box while allowing user customization
- When removing an agent tool but keeping its logic: extract the core function (no `RunContext` dependency), keep the tool function as a thin wrapper. This preserves testability and allows internal reuse

---

## ISSUE-066: Post-065 Audit Cleanup (2026-04-12)

### What worked well
- Running 5 parallel Explore sub-agents (architecture, tests, dead code, philosophy, docs) from a single audit prompt gave a complete picture in one round. Each agent stayed focused because its brief was narrow and self-contained — no cross-contamination, no duplicated reads.
- Splitting a large god-tool (`tools/marcel.py`) into a package with the dispatcher in one file and each action group in its own module kept the single-tool-to-the-LLM contract intact while fixing the maintainability problem. The `__init__.py` re-exports mean all existing imports (`from marcel_core.tools.marcel import marcel`) continue to work untouched.
- Extracting `TurnState` as a composed field on `MarcelDeps` (not inheritance, not a separate context parameter) meant tools only changed one line each (`deps.notified` → `deps.turn.notified`) and pydantic-ai's `deps_type` contract was unaffected.
- Writing the issue with all 8 tasks declared up front, then working them top-to-bottom, kept the commit sequence clean: one `📝 created`, two `🔧 impl` (code + linter fixup), one `✅ closed` (docs + issue move).

### What to do differently
- The docs site was already broken before this issue (`docs/index.md` missing from mkdocs.yml for weeks). Earlier audits should have run `mkdocs build --strict` as a sanity check — missing nav files are the kind of bug that only surfaces when someone actually views the site.
- Two documentation pages (architecture.md's memory extraction section, jobs.md's TriggerSpec table) had been stale since ISSUE-049 and ISSUE-064 respectively. The feature development procedure says "Update all affected doc pages in the same change as the code" — neither issue's closing commit caught the downstream doc reference. A grep for the changed module/field name across `docs/` at close time would have caught both.
- The `agent/` folder was named in ISSUE-033 (`marcel-md-system`) when it only held `marcelmd.py`, then it accreted `memory_extract.py` in ISSUE-049 without anyone noticing the name no longer fit. Module names should be revisited whenever a second file is added — if the name doesn't describe both, it probably shouldn't be the home for either.

### Patterns to reuse
- **Parallel audit pattern**: for any "deep audit / review since X" request, launch 4–6 focused Explore sub-agents in a single batch (architecture, tests, dead code, philosophy, docs, and optionally security). Each agent gets a self-contained brief with category-specific questions. Results come back in a few minutes and compile into a comprehensive report without polluting the main conversation with tool-call noise.
- **Composed state pattern**: when a dependency container starts accumulating mutable flags (`read_skills`, `notified`, `counter`, etc.), extract them into a `TurnState` / `RunState` dataclass composed as a field on the deps. Keeps the dep container immutable identity/config and collects all per-run state in one obvious place. Tools touch `deps.turn.x` instead of `deps.x`.
- **Package with dispatcher pattern**: when a single-file tool's action implementations grow past ~300 lines, convert the file into a package: `tool/__init__.py` re-exports the public entry point, `tool/dispatcher.py` holds the match/switch, and each action group lives in its own sibling module. Import paths stay stable thanks to `__init__.py` re-exports.
- **Doc-close verification grep**: before any closing commit, run `grep -r "<renamed function>" docs/ | grep -v closed_issue` to catch docs referencing the old name. Stale docs are worse than missing docs.

---

## ISSUE-067: A2UI Rendering Pipeline (2026-04-12)

### What worked well
- Reading the previous issue's closing notes (ISSUE-063) before scoping this work saved ~30 minutes of duplicated exploration — Phases 1–3 had already built the schema system, registry, `/api/components` endpoint, and the Mini App renderer with its A2UI fallback chain. The only missing piece was the agent-facing emission path, which collapsed a 10-task issue into ~50 lines of new code.
- Following the `generate_chart` side-effect pattern (validate → create artifact → `bot.send_message` with the Mini App button) was a dramatically smaller surface than a runner event-streaming refactor. The user got the exact user-visible outcome ("View in app" button in Telegram) without touching `stream_turn`, the Telegram webhook's `_collect()` loop, or the `ChannelAdapter` protocol.
- Writing explicit deferral reasoning into the task list (using the `[~]` marker and a written justification) made the scope-down decision auditable. Future maintainers can see exactly why the `ChannelAdapter` migration and runner event yield weren't touched, which makes picking them up later easier than if they had been silently dropped.

### What to do differently
- The initial issue description listed 10 tasks as if all were required for the MVP, when really only 4–5 were. When scoping an issue that sits on top of already-built infrastructure, the task list should distinguish "required for end-to-end" from "nice-to-have consistency cleanup" up front — otherwise the closing diff looks half-finished when it's actually complete-for-MVP.
- Didn't notice that the `~/.marcel/skills/banking/SKILL.md` and `~/.marcel/channels/telegram.md` data-root copies were stale relative to the bundled defaults until after editing the bundled versions. Seeding never overwrites existing files, so every time a default is updated, the running user's copy diverges silently. Should add a "refresh" mode to `seed_defaults` that can diff and re-sync user copies against defaults, or at least warn loudly.
- The plan file (glistening-knitting-wombat.md) was written as a diagnosis + deferral recommendation, but the user said "start implementation yes" anyway — should have updated the plan file to reflect the executed scope before diving in, so the plan and the implementation log match.

### Patterns to reuse
- **Side-effect tool pattern**: for tools that need to deliver rich content to the user, the `generate_chart` pattern (tool runs synchronously, calls the channel's delivery API directly, returns a confirmation string to the model) is strictly simpler than streaming events through the runner. Use it whenever the channel supports direct delivery (HTTP API, WebSocket message) and the agent doesn't need the result for its next reasoning step.
- **Capability gating via a frozenset + helper function**: `_RICH_UI_CHANNELS = frozenset({...})` + `channel_supports_rich_ui(channel) -> bool` is a low-overhead way to gate behavior on channel capabilities without requiring full `ChannelAdapter` adoption. Single source of truth, O(1) lookup, trivially testable, and easy to extend when a new channel is added.
- **Prompt injection that reuses already-loaded state**: when adding a new prompt section derived from skills, load skills once and pass the list to multiple formatters rather than calling `load_skills()` again. `build_instructions_async` now calls `load_skills()` once and passes the result to both `format_skill_index` and `format_components_catalog` — avoids a second disk scan per turn.
- **Explicit deferral markers in issue task lists**: use `[~]` alongside `[✓]` and `[ ]` to mark "consciously deferred" tasks, with a one-line written justification. Distinguishes "we chose not to do this" from "we forgot this" at review time, and the deferred tasks become pre-scoped follow-up work.

---

## ISSUE-068: System Prompt Restructure — Five H1 Blocks + Dynamic Memory (2026-04-12)

### What worked well
- **Event-log-driven scoping.** The user opened `event-log.md` (a Phoenix trace export of a real Telegram turn) and pointed at four concrete problems. Because the evidence was already rendered in front of both of us, the investigation collapsed from "explore memory/skill/prompt architecture" to "confirm or refute each of these four observations." The result: a thorough issue with no speculative scope.
- **Symmetric tool design.** Adding `read_memory` alongside the existing `read_skill` created a clean index-plus-on-demand pattern: the prompt contains a one-line-per-entry catalogue, and either `read_skill` or `read_memory` loads the full body when needed. Users (and the model) can reason about skills and memory the same way, which keeps the prompt footprint small without hiding either capability.
- **Load-time stripping instead of file edits.** Every cosmetic cleanup — duplicate H1s in `profile.md`, the self-referential blockquote in `MARCEL.md`, the `"You are responding via Telegram."` preamble in `telegram.md` — is done in the prompt builder via small stripper functions (`_strip_leading_h1`, `_strip_self_ref_blockquote`, `_strip_channel_preamble`). The on-disk files stay natural and user-editable, and the cleanup survives a `seed_defaults` refresh. This was a direct application of the lesson from ISSUE-067 about data-root drift.

### What to do differently
- **Phoenix trace truncation is not a runtime bug.** The `read_skill` result in `event-log.md` was cut off at ~200 characters mid-word, which made it look like skills were being truncated in the system. They weren't — the truncation is introduced by the OpenInference span processor serializing tool results into OTel span attributes (`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` defaults to 128 bytes on some exporters). The model receives the full string, only the trace viewer is lying. **Lesson:** when length mismatches show up in Phoenix, inspect the actual model message stream (pydantic-ai events, `ModelRequest.parts`) before chasing runtime bugs. The trace viewer is a diagnostic surface, not ground truth.
- **`SELECTION_THRESHOLD = 10` in `memory/selector.py` meant the AI memory selector was never actually running for typical users.** The branch at `selector.py:77` loaded ALL memories when `len(headers) <= 10`, and the threshold was high enough that real users stayed below it forever. The AI selector existed in the code and in tests but never touched production prompts. **Lesson:** when adding a "fallback for small inputs" threshold, double-check whether the fallback or the main path is the 99% case. If the fallback dominates, the main path is dead code — either delete it or flip the default.

### Patterns to reuse
- **Index + on-demand read pattern.** For any content type where users have many items but only a few are relevant per turn (skills, memory files, RSS feeds, old conversations), emit a compact index in the system prompt (`- **name** — description`) and provide a `read_<type>(name)` tool action that returns the full body. Scales to hundreds of entries without blowing the context budget, and the model learns to fetch precisely what it needs.
- **Five H1 blocks as a prompt contract.** The new system prompt structure — `# <Identity> — who you are`, `# <User> — who the user is`, `# Skills — what you can do`, `# Memory — what you should know`, `# <Channel> — how to respond` — reads like a coherent document instead of a pile of concatenated fragments. Each H1 answers a question the model is implicitly asking. Reuse this "headers as questions" framing for any multi-source prompt assembly.
- **Defensive re-stripping at the prompt builder.** `format_marcelmd_for_prompt` already strips leading H1s, but the prompt builder calls `_strip_leading_h1_safe` *again* before wrapping content under its own H1. Redundant by design: it means either the loader or the builder can be the stripper without coupling them tightly, and it keeps the builder robust against un-cleaned inputs from other loaders later.

---

## ISSUE-074: Subagent Delegation Tool (2026-04-13)

### What worked well
- **Feasibility investigation before the issue file.** Spawning two parallel Explore agents — one against the Marcel repo, one against `~/repos/clawcode` — before writing a single line of code produced a side-by-side architectural mapping that made the issue task list concrete and the scope decisions obvious. The "3 days / 22 tasks" estimate held almost exactly because the unknowns had been flushed out at feasibility time, not during implementation.
- **Mid-impl scope refinement logged as a first-class decision.** Cutting the `execute_job` reuse plan once it became clear it was fighting the persistence layer — and logging the cut in the issue's Implementation Log *before* writing the replacement code — kept the plan and the diff coherent. Future readers see why the delegate tool builds a fresh `Agent` directly instead of going through the job executor.
- **Single source of truth for tool registration.** Replacing the hand-written `agent.tool(core_tools.bash); agent.tool(core_tools.read_file); ...` sequence with a `_TOOL_REGISTRY: list[tuple[name, fn, required_role]]` and a single registration loop gave the feature a clean extension point (`tool_filter: set[str] | None`) without a conditional forest. Also surfaced the role-gate-beats-allowlist invariant as a single `if` in the loop.

### What to do differently
- **Don't write test assertions against `agent is not None` when you can introspect.** My first pass at `TestToolFilter` asserted only `assert agent is not None`, mirroring the existing style in `test_agent.py`. Running a quick one-liner against pydantic-ai's internals revealed `agent._function_toolset.tools: dict[str, Tool]` — after that, the tool_filter tests could verify exact registered sets. The stronger assertions would have caught a bug where the role gate ran *after* the allowlist instead of before. **Lesson:** when writing tests for a "filter" behavior, always find the shape of the output first; weak assertions on filter tests give false confidence.
- **The issue task list inflated the scope in advance.** 22 tasks was honest but overwhelming — including v1 + deferred items side by side made the "done" state feel further away than it was. Next time, use two sibling lists or a distinct `[~]` deferred state in the initial issue so the in-scope work is visually smaller than the aspirational work. ISSUE-068's `[~]` pattern was the right call and I should have reached for it from the start.

### Patterns to reuse
- **`_TOOL_REGISTRY` pattern for pluggable tool pools.** When a factory function wires up a fixed set of capabilities to a framework object (pydantic-ai Agent, FastAPI app, etc.), lift the list into a `list[tuple[name, obj, role_or_gate]]` at module scope and register in a single loop. Filtering, role gating, and introspection for tests all fall out for free, and adding a new tool is a one-line append instead of an edit to the factory body.
- **Recursion guard as a default-off pool entry.** The `delegate` tool is in the admin-role pool but gets stripped from subagent pools unless the subagent's frontmatter explicitly lists it. Encoding "opt-in for recursion" in the frontmatter (rather than a separate `allow_recursion: true` flag) means there's one mental model — the `tools` allowlist — not two. Reuse this shape whenever a capability is dangerous-by-default but legitimately useful in narrow cases.
- **Fresh-deps construction via `dataclasses.replace` + explicit `TurnState()`.** When a tool needs to spawn a child context that inherits identity (user, role, channel) but not per-turn state (notified flag, counters), `dataclasses.replace(ctx.deps, turn=TurnState(), ...)` is the clean idiom. Copies the immutable fields, zeros the mutable state, no hand-written field list, and it's obvious in the diff what's being carried forward vs reset.
- **Agent markdown with YAML frontmatter as a plugin format.** The same format used for skills (`SKILL.md`) works unchanged for agents (`<name>.md`) — both are "human-editable config that lives at the data root and seeds from defaults". Adopt markdown-with-frontmatter as the default plugin format in this codebase; anything that needs per-entry config with a free-form body slots in naturally and users can edit it by hand.
