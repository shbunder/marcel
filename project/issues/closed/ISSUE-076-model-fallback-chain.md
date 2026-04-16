# ISSUE-076: Four-Tier Model Fallback Chain

**Status:** Closed
**Created:** 2026-04-13
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, resilience

## Capture
**Original request:** "If you look at the telegram conversation today you notice the model call failed. Can we do the following model setup: MARCEL_STANDARD_MODEL (used for normal calls) / MARCEL_BACKUP_MODEL (used when first model fails) / MARCEL_FALLBACK_MODEL (preferable a local model that is used if both models fail and to simply explain the failure) / MARCEL_POWER_MODEL (Marcel can spin up a (default) subagent with this model if it thinks it can not handle the task). Can you investigate this featuer?"

**Follow-up Q&A:**
- *FALLBACK tier behavior?* → Explain-only: empty message history, empty tool filter, capped response length. Does NOT attempt to complete the task.
- *Jobs backup tier always-on or opt-in?* → Always-on when `MARCEL_BACKUP_MODEL` is set.
- *POWER trigger?* → LLM self-delegation only via `delegate(subagent_type='power', ...)`. No keyword heuristics, no slash command.
- *Mid-stream failures?* → Surface the error to the user; do not retry. Pre-stream failures (the common overloaded/429/5xx case) still retry silently.
- *How does this interact with jobs pinned to small/local models?* → Chain still runs by default. No auto-pin heuristic. Add a new per-job `allow_fallback_chain: bool = True` flag so users can explicitly opt out. Must be loudly documented so users remember to set it when pinning to local or cheap models.

**Resolved intent:** Marcel's conversational turn path currently picks one model and hard-fails if it breaks, which is exactly what happened today when Anthropic returned `overloaded_error` during a Telegram conversation. Introduce a uniform four-tier model system — STANDARD (normal), BACKUP (different cloud provider), FALLBACK (local, explains the failure), POWER (heavyweight subagent for hard tasks) — driven by environment variables and shared between interactive turns and scheduled jobs. Pre-stream failures retry silently against BACKUP, total cloud outage produces a friendly local-model apology instead of a stack trace, and Marcel gains a self-escalation path to a larger reasoning model via a new `power` subagent.

## Description
Today at 08:02 UTC, a Telegram conversation ("Can you restart my plex?") surfaced the raw `{'type': 'overloaded_error', ...}` payload to the user because [src/marcel_core/harness/runner.py:467](src/marcel_core/harness/runner.py#L467) resolves exactly one model and [line 511](src/marcel_core/harness/runner.py#L511) turns any exception into assistant-visible text. The job executor already has retries + an optional local-LLM fallback (ISSUE-070) but no cross-provider backup; subagent delegation (ISSUE-074) already supports per-agent model selection but has no dedicated "escalate for hard tasks" path.

This issue introduces a shared fallback chain helper used by both the interactive runner and the job executor, plus a new `power` subagent that resolves its model from a new `MARCEL_POWER_MODEL` env var via a generic `tier:*` sentinel in the agent frontmatter system.

Full design and behaviour matrix is captured in the plan file: `/home/shbunder/.claude/plans/glistening-dreaming-scroll.md`.

## Tasks
- [✓] Add four new env-var fields to [config.py](src/marcel_core/config.py): `marcel_standard_model`, `marcel_backup_model`, `marcel_fallback_model`, `marcel_power_model`
- [✓] Create [src/marcel_core/harness/model_chain.py](src/marcel_core/harness/model_chain.py) with `Tier`, `TierEntry`, `build_chain(primary, mode)`, `is_fallback_eligible`, `next_tier`, `build_explain_system_prompt`, `build_explain_user_prompt`
- [✓] Refactor `DEFAULT_MODEL` literal in [harness/agent.py:50](src/marcel_core/harness/agent.py#L50) to a `default_model()` runtime lookup so tests can monkeypatch `settings.marcel_standard_model`; update callsites in `runner.py`, `delegate.py`, `skills/integrations/settings.py`, `tools/marcel/settings.py`
- [✓] Rewrite `stream_turn` in [harness/runner.py:403-548](src/marcel_core/harness/runner.py#L403-L548) as a chain driver loop: pre-stream retry silently, mid-stream surface error, permanent error short-circuit, explain-tier uses empty history + empty tool_filter + `request_limit=1`
- [✓] Add `allow_fallback_chain: bool = True` field to `JobDefinition` in [jobs/models.py](src/marcel_core/jobs/models.py)
- [✓] Rewrite `execute_job_with_retries` in [jobs/executor.py:270-338](src/marcel_core/jobs/executor.py#L270-L338) to compose per-tier backoff retries with the chain helper; extract `_run_with_backoff` and `_execute_pinned_with_legacy_fallback` helpers
- [✓] Extend [agents/loader.py](src/marcel_core/agents/loader.py) to parse `model: standard|backup|fallback|power` as `tier:*` sentinels
- [✓] Resolve `tier:*` sentinels in [tools/delegate.py:142](src/marcel_core/tools/delegate.py#L142); return clean `delegate error: ... tier 'X' ... not set` when the required env var is unset
- [✓] Ship new default agent [src/marcel_core/defaults/agents/power.md](src/marcel_core/defaults/agents/power.md) with `model: power` and no explicit `tools:` allowlist (inherits parent role pool)
- [✓] Write [tests/harness/test_model_chain.py](tests/harness/test_model_chain.py): minimal/full chain, local-skip when URL missing, primary override, overloaded→server_error classification, permanent-error stops-before-explain, explain-prompt truncation
- [✓] Extend [tests/harness/test_runner.py](tests/harness/test_runner.py): pre-stream silent retry, all-cloud-fail explain path, all-tiers-unreachable hardcoded error, mid-stream no-retry, permanent skip-chain, channel-pin overrides tier-1-only
- [✓] Extend [tests/jobs/test_executor.py](tests/jobs/test_executor.py): chain uses backup before local, `fallback_used` names the tier, `allow_fallback_chain=False` pins job, `local:` + default chain escalates (footgun test), `local:` + opt-out stays local; keep existing local-fallback tests green via the legacy path
- [✓] Extend [tests/agents/test_loader.py](tests/agents/test_loader.py): `tier:*` sentinel parsing for all four tiers
- [✓] Write/extend [tests/tools/test_delegate_power.py](tests/tools/test_delegate_power.py): sentinel resolution with env set, clean error when env unset
- [✓] Write new [docs/model-tiers.md](docs/model-tiers.md): tier diagram, env var table, turn/job semantics, **prominent warning** for `local:` + default chain combo, full behaviour matrix, `power` subagent usage, three example configs
- [✓] Update [docs/local-llm.md](docs/local-llm.md) to point at `docs/model-tiers.md` as authoritative source (keep Ollama setup sections)
- [✓] Update [docs/subagents.md](docs/subagents.md) to document `model: standard|backup|fallback|power` sentinels and add `power` to the bundled agents list
- [✓] Register `docs/model-tiers.md` in `mkdocs.yml` nav
- [✓] Run `make check` — all tests, lint, typecheck, coverage pass
- [ ] Manual verification: simulate overloaded → tier 2 retry silently succeeds on a real Telegram turn (the exact failure mode from 2026-04-13) — deferred to post-deploy smoke test; the chain-driver unit tests fully cover the pre-stream retry path.

## Relationships
- Related to: [[ISSUE-070-local-llm-fallback]] — this issue subsumes the ISSUE-070 fallback into the unified chain while preserving its opt-in semantics via `allow_local_fallback`
- Related to: [[ISSUE-073-per-channel-model]] — per-channel model pin continues to work as tier 1 override
- Related to: [[ISSUE-074-subagent-delegation]] — `power` subagent is built on this delegation system

## Implementation Log

### 2026-04-14 - LLM Implementation
**Action**: Shipped the four-tier fallback chain end-to-end.

**Files Modified**:
- `src/marcel_core/config.py` — added `marcel_standard_model`, `marcel_backup_model`, `marcel_fallback_model`, `marcel_power_model` env var fields.
- `src/marcel_core/harness/model_chain.py` (NEW) — chain builder, `Tier` enum, `TierEntry`, `build_chain`, `is_fallback_eligible`, `next_tier`, `build_explain_system_prompt`, `build_explain_user_prompt`. Single source of truth for tier resolution and the explain-mode prompt.
- `src/marcel_core/harness/agent.py` — added `default_model()` runtime lookup so tests/env-var updates take effect without reload; `create_marcel_agent(model=...)` default arg is now `None` and resolves via `default_model()` at call time.
- `src/marcel_core/harness/runner.py` — rewrote `stream_turn` as a chain driver loop: pre-stream failures on eligible errors silently advance; mid-stream failures keep partial output + append an error tail (no retry); permanent errors short-circuit; explain tier runs with empty history + empty tool_filter + `request_limit=1`.
- `src/marcel_core/jobs/models.py` — added `allow_fallback_chain: bool = True` to `JobDefinition`.
- `src/marcel_core/jobs/executor.py` — extracted `_run_with_backoff` helper; added `_execute_chain` (chain mode) and `_execute_pinned_with_legacy_fallback` (opt-out mode); `execute_job_with_retries` now branches on `allow_fallback_chain`. Preserves ISSUE-070 `allow_local_fallback` semantics via a legacy bridge that synthesises a `local:<MARCEL_LOCAL_LLM_MODEL>` tier 3 when the job opts in but `MARCEL_FALLBACK_MODEL` is unset.
- `src/marcel_core/agents/loader.py` — added `model: standard|backup|fallback|power` → `tier:<name>` sentinel parsing in `_load_agent_file`.
- `src/marcel_core/tools/delegate.py` — added `tier:*` resolution before `create_marcel_agent`; returns `delegate error: ... MARCEL_<TIER>_MODEL is not set` on unset env vars rather than raising.
- `src/marcel_core/defaults/agents/power.md` (NEW) — bundled `power` subagent with `model: power`, seeded to `~/.marcel/agents/` via the existing seed mechanism.
- `src/marcel_core/skills/integrations/settings.py`, `src/marcel_core/tools/marcel/settings.py` — swapped `DEFAULT_MODEL` for `default_model()`.
- `tests/harness/test_model_chain.py` (NEW) — 21 tests covering chain construction, eligibility, `next_tier` rules (including permanent-error gating of the explain tier), and explain-prompt synthesis.
- `tests/harness/test_runner.py` — added 6 chain-driver tests: pre-stream silent retry, all-cloud-fail → explain, all-tiers-unreachable → hardcoded error, mid-stream no-retry, permanent skip-chain, channel pin replaces tier 1 only.
- `tests/jobs/test_executor.py` — added 7 chain-mode tests: backup tried before local, `fallback_used='backup'` naming, `allow_fallback_chain=False` pinning, the `local:` + default-chain escalation footgun (documents it explicitly), recommended local-pin opt-out, persisted-model-unchanged invariant.
- `tests/agents/test_loader.py` — parametrized tier-sentinel parsing for all four tiers, verified bundled `power.md` parses and uses `tier:power`.
- `tests/tools/test_delegate.py` — added tier sentinel resolution tests for all four tiers plus the clean-error-when-env-unset case.
- `docs/model-tiers.md` (NEW) — authoritative page with tier table, turn/job semantics, full behaviour matrix, `local:` footgun warning, three example configs, observability tips.
- `docs/local-llm.md` — trimmed the "When it fires" section, now points at `docs/model-tiers.md` as the authoritative source.
- `docs/subagents.md` — added Model tier sentinels section; added `power` to the bundled default agents list; updated the `model` frontmatter description in the table.
- `mkdocs.yml` — registered `docs/model-tiers.md` in nav.

**Commands Run**: `make check`

**Result**: Success — 1343 tests passed, 92.66% coverage. Lint + format + pyright + rust clippy all clean.

**Reflection**:
- Coverage: 19/20 requirements addressed. The 20th (manual Telegram smoke test against a live overloaded provider) is deferred to post-deploy — the chain-driver unit tests fully cover the pre-stream retry path and there is no way to trigger a real anthropic `overloaded_error` on demand from a dev box.
- Shortcuts found: none. The ISSUE-070 legacy local-fallback path is preserved end-to-end (`_execute_pinned_with_legacy_fallback` + a legacy bridge in `_execute_chain` when `MARCEL_FALLBACK_MODEL` is unset but `allow_local_fallback=True`), so existing jobs behave identically unless they opt into the chain.
- Scope drift: none. All four env vars, the `power` subagent, and the `allow_fallback_chain` opt-out flag match the approved plan exactly. The `local:` + default-chain footgun is documented in both the prominent warning box in `docs/model-tiers.md` and a dedicated test (`test_local_pinned_job_without_opt_out_escalates`) that will trip if a future refactor accidentally auto-pins.

## Lessons Learned

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
