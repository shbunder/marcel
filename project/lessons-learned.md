# Lessons Learned (active)

Captured after completed issues. Grep here and in [lessons-learned-archive.md](./lessons-learned-archive.md) at the start of new feature work to avoid repeating past mistakes and reuse proven patterns.

## Maintenance rule

Keep at most **10 active entries** in this file. When `/finish-issue` appends a new one, move the oldest to [lessons-learned-archive.md](./lessons-learned-archive.md) in the same commit. Do not let this file accumulate — always-loaded lessons compete for context window with the code being reviewed.

The archive is read on demand via `grep`, never loaded in full. See [project/FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) Step 1 for the grep pattern.

---

## ISSUE-079: Claude Code Setup Redesign (2026-04-15)

### What worked well
- Plan-mode dialogue with user pushback surfaced the real constraints — the first "commit-hash derived ID" design was over-engineered; the user's "can't we just generate a unique hash ourselves?" simplified it to a single creation commit.
- Extracting detail from CLAUDE.md files into `FEATURE_WORKFLOW.md`, `TEMPLATE.md`, `GIT_CONVENTIONS.md` preserved all the content while cutting always-loaded context 61% (479 → 186 lines).
- Reference-exploration with 3 parallel Explore agents (current setup, issue workflow, `~/repos/agent-skills`) was fast and gave an honest audit including the smoking gun: a past merge commit literally saying "preserving during ISSUE-075 close" — evidence of the very parallel-agent conflict the redesign fixes.

### What to do differently
- Almost went in circles trying to make `ISSUE-{git-short-hash}` work cleanly before the user cut through it by suggesting self-generated hashes. Lesson: when the user asks "how would this work?!", assume they're genuinely uncertain and push back on the complexity instead of rationalizing a Rube Goldberg solution.
- Meta-issues (issues that modify the issue workflow itself) create a chicken-and-egg: ISSUE-079 couldn't use the branch-per-issue flow because that flow didn't exist yet. Document this explicitly as a "self-exception" in the Reflection block and move on — don't try to bootstrap cleverly.

### Patterns to reuse
- Anti-rationalization tables (borrowed from `~/repos/agent-skills`) belong in any skill/CLAUDE.md where agents are tempted to take shortcuts. Structure: two columns — Excuse | Reality.
- Progressive disclosure: put short rules in CLAUDE.md (always-loaded), extract detailed process content into sibling reference files that are linked rather than loaded.
- Enriched skill frontmatter with `name` + explicit "do NOT use" exclusions, not just a description. Prevents the harness from invoking skills for inappropriate tasks.
- Self-generated 6-char hex IDs (`secrets.token_hex(3)`) with a collision retry loop solve parallel-allocation problems without needing a central counter or shared lock.

---

---

## ISSUE-0554d9: Parallel-agent git worktrees (2026-04-15)

### What worked well
- User caught a real gap in ISSUE-079 by asking the right question: "what will happen if 2 claude-code sessions are locally working on Marcel?" The commit-history isolation (branches + hash IDs) does not address working-directory isolation. Worktrees fix that. This is a good reminder that "parallel-safe" is a spectrum.
- Dry-running `git worktree add /tmp/marcel-scratch-worktree HEAD` followed by `git worktree remove` before shipping caught zero bugs but gave real confidence that the skill instructions would actually work. Small scratch tests are cheap insurance.
- First real end-to-end run of the new ISSUE-079 workflow (branch-per-issue + hash IDs) shipped cleanly: `📝 create` on main → branch → `🔧 impl` on branch → `✅ close` on branch → `--no-ff` merge → `🩹 fixup` on main for lessons. `git log --graph` shows the expected shape.

### What to do differently
- `git mv` after a Read call breaks the Edit tool's "you must Read first" precondition because the file path changes. Either Read the file again at its new location before editing, or do all the edits BEFORE the `git mv`. The latter is cleaner.
- Don't overclaim parallel-safety in docs. Say exactly what the mechanism prevents — hash IDs prevent counter collisions, branches isolate commit history, worktrees prevent working-directory collisions. Users will trust the docs more if they distinguish these.

### Patterns to reuse
- Two-skill variants for "light default" vs "heavier opt-in" — here: `/new-issue` (simple single-checkout) and `/parallel-issue` (worktree). Better than one skill with a flag because skill descriptions are what the harness matches on when deciding which to invoke.
- Worktree detection via `git worktree list --porcelain | awk '/^worktree / {print $2; exit}'` compared to `git rev-parse --show-toplevel` is reliable and doesn't need any state outside git.
- When documenting a feature-branch merge flow, always note `git worktree remove` can't run from inside the worktree being removed. The skill must `cd` to the primary checkout first.

---

---

## ISSUE-077: Post-076 audit cleanup — shame bump (2026-04-14)

### What worked well
- **Running the audit as four parallel Explore agents instead of one sequential sweep.** Architecture, tests, dead-code/navigability, and docs/philosophy are independent dimensions with different search patterns and different smells. A single agent would have needed four passes through the same files; four agents returned focused 700-word reports I could synthesise into one punch list without re-reading code. Main context was never polluted with raw grep output. **Reuse pattern:** for any "deep audit" request where the dimensions are independent, fan out by dimension, not by file.
- **Fixing the pre-existing `TestLocalFallback` breakage under the *right* issue, not the shame bump.** Four failing tests were left over from ISSUE-076 — they broke the moment the chain helper was introduced and nobody noticed. Rather than absorb the fix into ISSUE-077, I committed the one-line fix as a `🩹 [ISSUE-076] fixup` before starting the shame-bump implementation. Clean attribution for future `git log --oneline` readers. **Reuse pattern:** when a pre-commit hook surfaces a failure from an earlier issue, fix it under that issue's number — don't launder the cleanup through the issue you happen to be working on.
- **Preserving external error-string contracts when refactoring internals.** `delegate.py`'s tier-sentinel resolution moved from an inline dict to `resolve_tier_sentinel` raising `TierNotConfigured`, but `TestTierSentinelResolution` asserts substrings like `'MARCEL_BACKUP_MODEL'` and `'delegate error'`. The new exception carries `exc.tier` so delegate formats the error string byte-for-byte the same — zero test churn on a refactor that touched the whole tier pipeline. **Reuse pattern:** when refactoring an error path, read the tests' string assertions *first* and treat them as the observable contract you preserve, not an afterthought.

### What to do differently
- **Flipping a shared test-helper default instead of overriding at call sites.** My first attempt at fixing the pre-existing test breakage changed `_make_job`'s default to `allow_fallback_chain=False`, which immediately broke `TestFallbackChain` (which relied on the other default). Revert + per-site override. **Lesson:** when a shared helper is consumed by tests that expect different defaults, the fix goes at the call site, not in the helper. Changing a shared default is the kind of cross-cutting edit that always breaks something you can't see from where you're standing.
- **The task list said "prime `turn.read_skills` from message history at the top of `execute_job`".** It echoed the runner's implementation without thinking about the job path — jobs have no history. Had to diverge from the literal task wording (prime from the job's resolved skills instead) and document the divergence in the issue's "Out-of-scope notes". **Lesson:** when writing tasks for an audit-cleanup issue, describe the *invariant you want* ("the integration tool must not duplicate already-injected skill docs") rather than the *mechanism you imagined* ("mirror the runner's prime-from-history call"). Mechanism-level tasks silently propagate the wrong assumption.

### Patterns to reuse
- **Audit finding → commit chunking 1:1.** Implementation split into four commits: (1) test rot, (2) architecture (backwards import + tier sentinel + read_skills priming), (3) docs (SETUP + README + subagents), (4) ruff reflow. Each commit message lists the specific findings it addresses. `git log --oneline` now reads as a punch-list check-off for anyone reviewing the shame bump. **Reuse pattern:** when fixing a cluster of independent findings, chunk commits by *kind of concern* (tests / architecture / docs) rather than by file — the diff is easier to review and the messages double as a progress log.
- **Re-export symbols from their old home for test-import backwards compat.** After moving `classify_error` + `FALLBACK_ELIGIBLE_CATEGORIES` from `jobs/executor.py` to `harness/model_chain.py`, I left an explicit re-export + `__all__` block in `executor.py` with a comment pointing at the new home. Zero test churn, and the re-export is a documentation breadcrumb for the next reader who goes looking in the old place. **Reuse pattern:** when relocating a public symbol that tests import, leave a redirect import with a comment — don't update the test imports unless the relocation is user-facing API.
- **"Shame bump" as a recognised issue type.** Previous post-audit cleanup (ISSUE-066 after 065, ISSUE-077 after 076) both followed the same shape: bundle audit findings, explicitly mark scope boundaries, track deferred items in the issue file rather than losing them. The SHAME version segment anticipates this. **Reuse pattern:** after any multi-issue feature cluster (3+ consecutive issues in one area), schedule a post-cluster audit and fold the findings into a single shame bump. Don't let the rot accumulate across shippable milestones.

---

## ISSUE-076: Four-Tier Model Fallback Chain (2026-04-14)

### What worked well
- **Asking the user a second round of clarifying questions when a plan assumption turned out to be shakier than expected.** The initial plan had "jobs always use the chain" as a confirmed decision — but the user's follow-up ("jobs typically use a smaller 'standard' model, sometimes even a local model") revealed a real footgun (local-pinned jobs silently escalating to cloud). Surfacing the concrete trade-off via AskUserQuestion instead of picking a default produced the `allow_fallback_chain` flag + the documented warning. **Lesson:** when a user question during planning reveals you never thought about a whole class of inputs, that's a signal to pause and re-ask, not to guess.
- **Pre-stream vs mid-stream as the only streaming retry rule.** The runner rewrite spent a lot of design energy on "when can we silently swap models." Landing on "retry iff `committed == False`" (i.e., no tokens yielded yet) kept the driver loop small and made the common case — Anthropic `overloaded_error` raised by `run_stream()` before any token — just work. The mid-stream case is intentionally lossy (partial text + error tail) because retrying would either duplicate work or discard visible output. Pick one clear rule over a clever one.
- **Legacy bridge via synthesised tier entry.** ISSUE-070 jobs used `MARCEL_LOCAL_LLM_MODEL` + `allow_local_fallback=True`, not `MARCEL_FALLBACK_MODEL`. Rather than force users to set a new env var, `_execute_chain` detects the legacy combo and *synthesises* a `TierEntry(FALLBACK, local:<tag>, 'complete')` at run time. Zero user-visible migration, existing tests pass unchanged, and the mental model stays "the chain is the source of truth" inside the executor.

### What to do differently
- **A parallel agent session clobbered my uncommitted edits mid-implementation.** An agent working on ISSUE-075 did a git cleanup that wiped my staged ISSUE-076 changes along with its own. Everything had to be re-applied from memory. **Lesson:** commit early and often during multi-step implementation — don't accumulate 8 uncommitted file edits before the first `git commit`. One commit per logical step (config + new module + refactor + rewrite + tests + docs) would have made a concurrent-session collision a 10-second recovery instead of a full replay. Alternatively, work inside `git worktree` for anything non-trivial in shared repos.
- **Tier sentinels in frontmatter vs name-special-casing in delegate.py.** The plan briefly considered special-casing `subagent_type == 'power'` inside `delegate.py`. The `tier:<name>` sentinel approach (parsed in the loader, resolved in delegate at call time) is strictly better: it's reusable for any tier-backed agent, the resolution logic lives in one place, and the agent file itself tells a reader which tier it depends on. **Lesson:** prefer a generic vocabulary over a named special case even when there's currently only one caller — the marginal complexity is tiny and the extensibility is free.
- **Scheduling the "manual verification" task as a checkbox you can't actually tick in a dev box is bad task hygiene.** The final task was "simulate overloaded → tier 2 silently succeeds on a real Telegram turn" — there's no way to force Anthropic to return `overloaded_error` on demand. The unit tests cover the logic fully; the task should have been phrased as "post-deploy smoke test" from the start so it reads as deferred-by-design rather than incomplete.

### Patterns to reuse
- **`default_model()` function instead of a module-level constant.** Replacing `DEFAULT_MODEL = 'anthropic:...'` with a zero-arg function that reads `settings.marcel_standard_model` at call time solved the test-monkeypatch problem cleanly: `monkeypatch.setattr(settings, 'marcel_standard_model', ...)` just works, no module reload dance. **Reuse pattern:** whenever a "default" value comes from config that might change at runtime or during tests, expose it as a function, not a constant. Constants freeze at import time; functions don't.
- **`tier:<name>` sentinel resolved at call site.** The agent loader maps `model: power` → `tier:power` (a string that can't collide with any real `provider:model` pair), and `delegate.py` resolves it against `settings.marcel_*_model` right before `create_marcel_agent`. Env-var changes take effect on the next turn, no restart, no reload. **Reuse pattern:** for any "late-binding reference to a config value" in a declarative file, encode it as `<namespace>:<name>` and resolve at call time — don't pre-resolve at load time, and don't invent a new frontmatter type.
- **Tier-mode duality (`explain` vs `complete`).** The same chain helper powers interactive turns (`mode='explain'` — tier 3's purpose is to tell the user cloud is down, empty history, no tools, `request_limit=1`) and scheduled jobs (`mode='complete'` — tier 3 tries to finish the task like ISSUE-070). One builder function, one tier resolution rule, two purposes — and the runner / executor just pick the mode that fits their semantics. **Reuse pattern:** when a shared primitive serves two audiences with diverging last-resort behavior, parameterize the *purpose* of the last tier rather than forking the whole pipeline.
- **Documented footgun + a test that documents it.** The `local:...` + default `allow_fallback_chain=True` combo silently escalates to cloud. Rather than hide it, `tests/jobs/test_executor.py::test_local_pinned_job_without_opt_out_escalates` asserts the exact escalation happens, and `docs/model-tiers.md` has a prominent warning box. **Reuse pattern:** when you can't eliminate a footgun without surprising users in a different way, write a test that pins the current behavior and flag it loudly in the docs. The test prevents accidental "fixes" that would break other configs; the doc prevents user surprise.

---

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

---

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

---
