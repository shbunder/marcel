# Lessons Learned (active)

Captured after completed issues. Grep here and in [lessons-learned-archive.md](./lessons-learned-archive.md) at the start of new feature work to avoid repeating past mistakes and reuse proven patterns.

## Maintenance rule

Keep at most **10 active entries** in this file. When `/finish-issue` appends a new one, move the oldest to [lessons-learned-archive.md](./lessons-learned-archive.md) in the same commit. Do not let this file accumulate — always-loaded lessons compete for context window with the code being reviewed.

The archive is read on demand via `grep`, never loaded in full. See [project/FEATURE_WORKFLOW.md](./FEATURE_WORKFLOW.md) Step 1 for the grep pattern.

---

## ISSUE-1897a3: Adopt three rationalization/discipline patterns from agent-skills (2026-04-15)

### What worked well
- **Research-first framing avoided a wholesale import.** The user asked a question ("is any of this useful?"), not for an implementation. Parallel Explore agents surveying both repos in one turn produced enough material to write an honest tradeoff assessment rather than a shopping list, and the resulting scope was three tiny documentation edits instead of a new skills subtree. Default to answering the actual question, not the ambitious version of it.
- **Marcel's existing rule voice is strong enough to borrow the pattern without the jargon.** The agent-skills "Common Rationalizations" tables are excellent, but the agent-skills phrasing ("anti-rationalization", "Stop-the-Line") would have stuck out. Translating each table into Marcel's `| Excuse | Reality |` format — which was already in use in `project/issues/CLAUDE.md` — meant the tables read as native on the first pass; the pre-close-verifier confirmed zero tone drift.
- **Pre-close-verifier caught a context-loaded lessons-learned straggler the writer would have shipped.** The verifier flagged `project/lessons-learned.md:37` ("Four universal rules stay always-loaded") because it's always-loaded and a future reader would trust the count. The straggler grep the writer ran earlier had excluded `project/lessons-learned.md` by instinct. **Lesson:** when changing something that has a count anywhere, always-loaded files are the first place to check — they quietly become the source of truth for anyone who reads them later.

### What to do differently
- **Do not freeze live counts into always-loaded prose.** The stale phrase in `lessons-learned.md` was introduced one issue ago with *"Four universal rules stay always-loaded; the three domain-specific ones only load when relevant"* — a factual statement that aged out the moment a rule was added. Fixed by rewording to *"Universal rules stay always-loaded; domain-specific ones only load when relevant"*. Going forward: when writing lessons-learned entries, phrase observations qualitatively ("universal vs. domain-specific") rather than with raw counts that will drift.
- **`project/CLAUDE.md` editing requires the unlock flag even for one-line rule registrations.** The guard-restricted hook correctly blocked the edit; the unlock/edit/remove cycle worked but added one extra turn. **Lesson:** when planning a rule addition, bundle the `project/CLAUDE.md` registration together with all other edits, touch the unlock flag once, and delete it before staging so the impl commit cannot accidentally include it.

### Patterns to reuse
- **Adapt, don't import, when adopting patterns from another project's "skills" repo.** The agent-skills SKILL.md files are structured as standalone executable workflows; Marcel's rules are short, enforceable, and cross-referenced by subagents. Lifting the *structure* (rationalizations table, anti-excuse framing) without lifting the *words* produces additions that feel native and don't create two competing sources of truth.
- **Straggler grep must include `project/lessons-learned.md`.** The file is always-loaded and can easily contain frozen factual claims about the codebase state. Add it to the default grep scope in [docs-in-impl.md](../.claude/rules/docs-in-impl.md) whenever you change something referenced there. The pre-close-verifier is currently the safety net — don't rely on it.
- **Unique-string Edits on shared prose files beat line-based Writes.** The rationalizations-table insertions used `Edit` with a distinctive anchor (the `## Enforcement` header) as `old_string`, which means the edit is robust even if other parts of the rule file change between read and write. For any cross-file batch edit, anchor on the most locally unique string, not the first matching line.

---

## ISSUE-caf8de: Job storage — flat layout + SKILL.md-style JOB.md (2026-04-15)

### What worked well
- **Mirroring an existing shape is cheaper than inventing one.** Modeling `~/.marcel/jobs/<slug>/JOB.md` after the existing `~/.marcel/skills/<name>/SKILL.md` layout gave the user (and me) a format with zero cognitive overhead — frontmatter parser, directory conventions, and mental model were already understood. No need to justify or document the new format as novel.
- **Splitting mutable state into `state.json` at the serializer boundary, not as a separate pydantic model.** `JobDefinition` stays a single in-memory object; `save_job` partitions fields into frontmatter/body/state.json at write time via `_FRONTMATTER_FIELDS` / `_STATE_FIELDS` tuples, and `load_job` merges them back. One type, one source of truth, no synchronization logic — strictly simpler than a two-model split-and-merge approach I initially considered.
- **Pre-close-verifier caught three stragglers I would have shipped.** Two out-of-date docstrings in `models.py` and one unrunnable code example in `docs/local-llm.md`. The inline grep I did as part of step 7 would likely have missed the docstrings entirely — the subagent's independent pass on the diff surfaced them, and all three were 30-second mechanical fixes.

### What to do differently
- **Verify scheduler-managed state fields even when they look stable.** I nearly shipped `schedule_errors: 2` in JOB.md frontmatter because the test constructor treated it as user-authored; the partition boundary between frontmatter and `state.json` is the kind of thing a simple round-trip test catches but review wouldn't. Always add a "mutable field goes to state.json" assertion when you split storage.
- **`scripts/` is gitignored but tracked.** Wasted two minutes when `git add scripts/seed_jobs.py` failed with "ignored" and `git diff` showed nothing — turns out once-tracked files keep getting picked up by pyright and ruff (both modified the file), but `git add` needs `-f`. Add a quick `git check-ignore -v <path>` reflex any time a stage surprisingly fails.
- **The `_system` sentinel has a cache boundary leak.** I documented in `__init__.py` that "the real `~/.marcel/users/_system/` directory never exists" but the verifier spotted that `job_cache_write` / `job_cache_read` in `tool.py` would in fact create `~/.marcel/users/_system/job_cache/` if a system-scope job caches output. Cache was out of scope so I deferred, but the invariant should have been enforced (or at least asserted) at the cache boundary too. Sentinels need to be respected by every downstream path, not just the ones you remembered.

### Patterns to reuse
- **Field-partitioning serializer over split pydantic models.** When part of a pydantic model is user-authored and part is runtime-mutable, keep the model unified and partition at `save_job`/`load_job`. `dump = json.loads(job.model_dump_json()); fm = {k: dump[k] for k in _FRONTMATTER_FIELDS}; state = {k: dump[k] for k in _STATE_FIELDS}`. Clean, robust, no extra model types to keep in sync.
- **Sentinel slug for "no user" on a strict-typed field.** `SYSTEM_USER = '_system'` lets `users: []` jobs pass through `MarcelDeps.user_slug: str` without relaxing the type or threading `Optional` through 40+ call sites. The real `users/_system/` directory never exists, so per-user file lookups naturally return empty. Cheaper than `str | None` for "one of the values is special."
- **Directory name as stable identifier, name as mutable label.** `_resolve_slug` looks up the existing directory by `id` first — renaming a job rewrites JOB.md in place without moving the directory. Derives a fresh kebab-case slug (with `-2`, `-3` deduplication) only for new jobs. Matches how skills directories work.
- **One-shot migration inside `rebuild_schedule`.** Self-heal layout changes at scheduler startup: run `migrate_legacy_jobs()` before `_ensure_default_jobs()`, make it idempotent so the second boot is a no-op (`if not legacy_dir: return 0`). Users get the migration for free on their next restart; tests exercise the same code path.
- **Mock helpers must accept new kwargs proactively.** When adding `user_slug=None` keyword arg to `execute_job`, update every `async def mock_execute(j, reason='scheduled'):` to `async def mock_execute(j, reason='scheduled', *, user_slug=None):` before running tests. Pyright won't catch it; the first test run will flag it with an opaque `AsyncMock side_effect` error.

---

## ISSUE-0ee9fc: Extract enforceable rules to .claude/rules/ (2026-04-15)

### What worked well
- **Path-scoped rules are a genuine context win.** Scoping `integration-pairs.md`, `data-boundaries.md`, and `role-gating.md` to specific subtrees means sessions that don't touch those areas don't pay the context cost. Universal rules stay always-loaded; the domain-specific ones only load when relevant. This mapping — always-loaded for workflow safety, path-scoped for domain specifics — worked cleanly on the first pass.
- **Straggler grep caught real violations immediately.** Grepping `git add -A` across the tree found two lines in `project/plans/architecture-overview.md` that were prescribing a forbidden pattern. The grep was run as part of inline verification and the fix (marking the doc Superseded with an inline reference to the rule) took 30 seconds. This is exactly the kind of drift the rule is meant to catch, validated on the rule's own introduction commit.
- **Empirical confirmation that subagents do NOT hot-reload.** Previous issue's lesson predicted this; this issue proved it with a live `Agent(subagent_type="pre-close-verifier", ...)` call returning "Agent type not found". Now we know: hooks reload mid-session, subagents do not. This is a real constraint on how meta-issues can test their own changes.

### What to do differently
- **Meta-issues that introduce verification infrastructure cannot be verified by that infrastructure in the same session.** Plan for this: either ship the infrastructure in one issue and test-drive it on the next, or accept that the introducing issue will always use the inline fallback path. Don't burn cycles trying to make the new verifier verify itself.
- **`.claude/rules/` files need the YAML frontmatter to be valid as loaded.** I hand-wrote the `paths:` blocks; in retrospect a validator step (simple python YAML parse) would be cheap insurance against a typo silently breaking loading. Add to the next "setup hardening" pass if one happens.

### Patterns to reuse
- **Rule file structure.** Each rule file has sections: a one-line summary, "Never" (explicit bans), "Always" (explicit requirements), "Why" (rationale), and "Enforcement" (which subagent, what severity). The Enforcement section is machine-readable: the pre-close-verifier greps it to decide which rules to apply for a given diff. Keep this structure for any new rule.
- **Path-scoping to subtrees, not individual files.** `src/marcel_core/storage/**/*.py` is right; `src/marcel_core/storage/conversation.py` is too narrow — rules should survive file renames. Use glob patterns with `**` wildcards at the directory level.
- **Link from the old location to the new rule, not the other way around.** When trimming `GIT_CONVENTIONS.md` and `project/CLAUDE.md`, the workflow prose stayed and points AT `.claude/rules/<name>.md` as the source of truth. The rule file does not need to know about its former home. One-way links age better than two-way ones.
- **Superseded-doc pattern for historical content that violates new rules.** Rather than rewriting history, add a header note saying the document is obsolete, point at the current source, and flag the specific violation with an inline link to the relevant rule. Reader immediately knows the doc is not prescriptive.

## ISSUE-999fa7: Claude Code setup hardening (2026-04-15)

### What worked well
- The PreToolUse safety hook went from "written" to "caught a real edit" inside the same session — I edited `project/CLAUDE.md` to fix a dangling description, the hook blocked it, the unlock-flag dance worked exactly as documented. End-to-end validation from inside the issue that introduced the mechanism. Hard to get a better test than that.
- Pre-close verification (run inline because the subagent was registered too late in the session to be callable) caught a real shortcut: a bare `except Exception: pass` in the hook's JSON parse. The shortcut-hunt checklist from the pre-close-verifier SKILL.md earned its cost on its first use.
- Splitting into 6 small `🔧 impl` commits kept each one reviewable on its own. The pre-commit hook ran `make check` on every one — would have caught a regression early, didn't need to.

### What to do differently
- Claude Code reloads hooks/settings mid-session more eagerly than I expected — the safety hook I wrote went live during the same session that wrote it, not on next startup. Plan for that: if an in-session change would make YOUR OWN next edits harder, either unlock preemptively or sequence the edits so you finish the protected files before wiring up the guard.
- Subagent files were NOT picked up in-session (the Agent tool did not know `pre-close-verifier` by name even after the file was committed). Hooks reload fast, agents reload slow. Document this difference so the next issue that introduces a subagent knows not to rely on it until the session restarts.

### Patterns to reuse
- **Fail-open defensive hooks.** A broken hook that blocks all editing is worse than no hook at all. Explicit narrow-except with a comment (`# Fail open on malformed input so a broken hook never blocks all editing`) is the right pattern for any PreToolUse guard script.
- **Unlock-flag as a workflow, not a setting.** Three-step dance: `touch`, edit, `rm`. Gitignored so it cannot ship. Status line shows a 🔓 when set so "forgot to re-lock" is visible at a glance.
- **Lessons-learned rotation as part of `/finish-issue`.** Cap on active file (10 entries) + unbounded archive + grep-don't-read in FEATURE_WORKFLOW. Prevents the always-loaded context footprint from growing as Marcel accumulates institutional memory.
- **Subagent files adapted from `~/repos/agent-skills` are a great starting point** but need a Marcel-specific rewrite pass: the generic `code-reviewer.md` has no opinion on pydantic-ai vs other harnesses, on flat-file vs DB storage, or on Marcel's role-gated tool split — all of which matter for a real review. Same for `security-auditor.md`: generic OWASP is noise; Marcel's real attack surface is credential storage, Telegram webhook, restart flag, and browser SSRF.

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

---

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
- **Eliminated footgun with an automatic guard.** The `local:...` + default `allow_fallback_chain=True` combo used to silently escalate to cloud — ISSUE-b95ac5 added an executor guard that auto-forces `allow_fallback_chain=False` for local-pinned models. `tests/jobs/test_executor.py::test_local_pinned_job_auto_disables_chain` asserts the guard prevents escalation, and `docs/model-tiers.md` documents the automatic behavior. **Reuse pattern:** when a footgun can be eliminated by detecting the unsafe combination at runtime (local model + chain enabled), do so — a warning log is better than relying on users to remember a manual opt-out.

---

---

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

---

---


---

---

---

---


---
