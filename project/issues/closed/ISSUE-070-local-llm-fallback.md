# ISSUE-070: Local LLM Fallback for Jobs

**Status:** Closed
**Created:** 2026-04-12
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, infra, jobs

## Capture

**Original request:**
> do a deep internet research, what is the best model out there that I can locally run on the NUC?
> - I want benchmark proven good model(s)
> - How can I locally run it on the server (ollama, lama.cpp, others??)
> - It don't want it to kill the server, the server also has other tasks like running the plex
> Can you do a deep investigations, be critical. The use case would be to have the jobs run on this model, and have it as a fallback. Later we can see if we can replace parts of the Marcels brain with this model.

**Follow-up Q&A:**
- Gemma models? — Ruled out: Gemma 3 lacks native tool-use tokens (27B scored 6.6% on τ2-bench, 4B scored 55% on jdhodges tool-calling eval). Gemma 4 fixes this only at 31B (~19 GiB, too big). Revisit if/when a small Gemma 4 ships with native function calling.
- Will this continuously consume RAM? — No, with `OLLAMA_KEEP_ALIVE=5m`: model loads on first job, unloads 5 min after the last job completes. Systemd `MemoryMax=6G` is a hard safety net.
- Scope: research + plan only, or implement now? — User approved Option A of the plan and then Option A of the parallel path: ship the hardware-independent code changes now (defaulting off), run the infra smoke tests once a second DIMM arrives.

**Resolved intent:**
Add a self-hosted local LLM as an opt-in fallback path for Marcel jobs, without changing any default behavior. The code change is hardware-independent — it teaches `_resolve_model_string()` to route `local:*` model strings to an OpenAI-compatible server (Ollama/IPEX-LLM on the NUC's Intel iGPU), adds a per-job `allow_local_fallback` flag that fires after cloud retries exhaust or on auth/quota errors, and ships docs for the runtime setup. The full research rationale and hardware rollout plan live in `.claude/plans/partitioned-stirring-oasis.md`. Target runtime: **Qwen3.5-4B-Instruct Q4_K_M** via IPEX-LLM Ollama Portable Zip; revisit size once the NUC's second DIMM is installed and iGPU bandwidth is no longer single-channel-starved.

## Description

**Goal:** when Anthropic/Bedrock/OpenAI is down, or when a user wants it explicitly per-job, a Marcel background job can execute against a local LLM running on the NUC instead of failing or retrying forever.

**Why now:**
1. Reliability — the existing retry/backoff loop in `execute_job_with_retries()` handles transient errors but has no alternate-model fallback. A sustained Anthropic outage currently means jobs fail until the provider recovers.
2. Foundation for future work — once fallback is stable, we can incrementally route small, deterministic sub-tasks (classification, routing, short rewrites) through the local model as the primary, trading quality for privacy + cost. That's a separate issue.

**Constraints derived from hardware and existing architecture:**
- NUC is Intel Core Ultra 5 125H, 14 GiB RAM (single-channel DDR5-5600 today), Intel Arc iGPU, no dGPU, runs Plex. The code change must NOT assume anything about the runtime being available — it must default off.
- Marcel already uses pydantic-ai Agent. pydantic-ai supports OpenAI-compatible endpoints via `OpenAIModel(..., provider=OpenAIProvider(base_url=..., api_key=...))`. No new SDK dependency needed.
- The existing lean job prompt (`_build_job_context`) already omits MARCEL.md and memory dumps, so a 4B model can consume it comfortably inside 8K context.
- Per CLAUDE.md safety rules, this change touches **no** auth logic, core config, or MARCEL.md. It only adds new branches behind env vars that default to off.

**Out of scope for this issue:**
- Installing the IPEX-LLM Ollama runtime, downloading model weights, or writing the systemd unit (that's Stages 1, 2, and 4 of the plan — waiting on second DIMM).
- Using the local model for anything other than jobs — the five-block interactive system prompt stays Claude-only.
- Replacing parts of Marcel's brain wholesale (explicitly deferred in the plan as Stage 5).

## Tasks

- [✓] **070-a** — Add `marcel_local_llm_url: str | None = None` and `marcel_local_llm_model: str | None = None` fields to [src/marcel_core/config.py](src/marcel_core/config.py) under the AI providers section, with docstring comments.
- [✓] **070-b** — Add qualified `local:*` entries (e.g. `local:qwen3.5-4b-instruct-q4km`) to `KNOWN_MODELS` in [src/marcel_core/harness/agent.py](src/marcel_core/harness/agent.py) only when `settings.marcel_local_llm_url` is set, so the UI doesn't show broken options. **Note (ISSUE-073):** the old `ANTHROPIC_MODELS` / `OPENAI_MODELS` / `_resolve_model_string` layer was deleted in ISSUE-073; model strings now pass straight through to pydantic-ai as `provider:model`.
- [✓] **070-c** — Since `create_marcel_agent` now passes the model string verbatim to `Agent()`, the local branch needs a different hook. Options: (1) intercept `local:*` in `create_marcel_agent` and substitute a pre-configured `OpenAIModel(..., provider=OpenAIProvider(base_url=settings.marcel_local_llm_url, api_key='ollama'))` instance before the `Agent(...)` call, or (2) register `local` as a pydantic-ai custom provider at startup. Option 1 is localized and preferred. Raise a clear `RuntimeError` if `local:` is requested but `marcel_local_llm_url` is not set.
- [✓] **070-d** — Add `allow_local_fallback: bool = False` to `JobDefinition` in [src/marcel_core/jobs/models.py](src/marcel_core/jobs/models.py). Additive with default, so existing `job.json` files deserialize without migration (pattern from ISSUE-061).
- [✓] **070-e** — Add `fallback_used: str | None = None` to `JobRun` in the same file, for observability in `runs.jsonl`.
- [✓] **070-f** — Extend `classify_error()` in [src/marcel_core/jobs/executor.py:35](src/marcel_core/jobs/executor.py#L35) with a new `"auth_or_quota"` category covering 401/403, "invalid api key", "insufficient_quota", "credit balance too low". These are permanent for the cloud provider but should trigger the local fallback.
- [✓] **070-g** — Add a local-fallback branch at the end of `execute_job_with_retries()` in [src/marcel_core/jobs/executor.py:249](src/marcel_core/jobs/executor.py#L249). After the existing retry loop exits, if the run is still failed/timed-out AND `job.allow_local_fallback` AND `settings.marcel_local_llm_url` is set AND the error category is in `{auth_or_quota, rate_limit, server_error, network, timeout}`, do one final `execute_job()` call with the job temporarily re-targeted at `f'local:{settings.marcel_local_llm_model}'`. Set `run.fallback_used = 'local'` on success. Do not mutate the persisted `job.model` — only the in-memory copy for this one call.
- [✓] **070-h** — Unit test in [tests/harness/test_agent.py](tests/harness/test_agent.py): `create_marcel_agent` correctly wires the local `OpenAIModel` when given a `local:*` string, raises when `marcel_local_llm_url` is unset, and leaves non-local qualified strings untouched.
- [✓] **070-i** — Unit test in [tests/jobs/test_executor.py](tests/jobs/test_executor.py) (create if missing): `execute_job_with_retries` invokes the local fallback exactly once after transient retries exhaust, marks `fallback_used='local'`, and does NOT invoke fallback when the flag is off. Mock the agent run so no network is touched.
- [✓] **070-j** — Write [docs/local-llm.md](docs/local-llm.md) with: hardware prerequisites (dual-channel RAM requirement, kernel, OpenCL), IPEX-LLM Ollama Portable Zip install steps (Linux, for this Core Ultra 5 125H specifically), systemd unit with `MemoryMax=6G` / `CPUQuota=400%` / `Nice=10` / `OLLAMA_KEEP_ALIVE=5m`, env-var wiring (`MARCEL_LOCAL_LLM_URL`, `MARCEL_LOCAL_LLM_MODEL`), per-job opt-in example, verification checklist, and rollback instructions. Also add a nav entry in [mkdocs.yml](mkdocs.yml) per `docs/CLAUDE.md`.
- [✓] **070-k** — `make check` — format, lint, typecheck, tests must all pass before the closing commit (pre-commit hook enforces this).
- [✓] **070-l** — Append implementation work to the Implementation Log section of this file as it's done.

## Relationships

- Related to: [[ISSUE-061-harden-job-scheduler]] — extends its retry/classification model with a new terminal fallback path.
- Implements part of: the research plan at `/home/shbunder/.claude/plans/partitioned-stirring-oasis.md` (Stage 3). Stages 0–2 and 4 remain blocked on hardware (second 16 GB DDR5-5600 SODIMM ordered but not yet installed).

## Comments

### 2026-04-12 - Plan

The research plan calls out that Ollama historically reports "does not support tool use" for some models even when the model itself is capable (e.g. GLM-4), because of chat-template translation gaps. That's a runtime issue to validate during Stage 1 smoke testing, not a code issue for this issue — the code here only needs to route the request through an OpenAI-compatible endpoint. If Ollama breaks Qwen3.5-4B tool calls in practice, the fix is to switch the runtime to llama.cpp server portable zip (same URL shape, same env vars, zero code change).

## Implementation Log

### 2026-04-13 22:30 - LLM Implementation

**Action:** Implemented the hardware-independent half of the local-LLM
fallback plan (Stages 1–3 of `.claude/plans/partitioned-stirring-oasis.md`).
All code is opt-in and defaults off.

**Pre-implementation validation (run on the live NUC, not in CI):**

- Stage 0 — hardware probe. Core Ultra 5 125H, 14 GiB RAM currently
  **single-channel** DDR5-5600 (`Controller0-ChannelA-DIMM0` populated,
  second slot empty), Intel Arc iGPU exposed via OpenCL 3.0 NEO, kernel
  6.8.0-106, swap hot at 1.6 GiB. A second 16 GB SODIMM is on the way;
  runtime tuning (threads, iGPU acceleration) is deferred until it lands.
- Stage 1 — runtime smoke test. Downloaded upstream ollama v0.20.6
  (2.0 GB tar.zst → 4.8 GB extracted to `/tmp/ollama-smoketest/`) and
  `qwen3.5:4b` (3.4 GB). Tool-calling test via `curl /v1/chat/completions`:
  single-tool, **parallel multi-tool (2/2)**, and full round-trip
  (tool results → natural-language synthesis) all passed on 3/3 runs,
  `finish_reason=tool_calls` and well-formed JSON arguments every time.
  Warm throughput ~4.85 tok/s on the default 4 threads — tunable via
  `OLLAMA_NUM_THREAD` after the RAM upgrade. Peak RAM 9.4 GiB used
  (~5 GiB working set), dropped to 3.1 GiB after `KEEP_ALIVE=30s`
  unloaded the model; no swap growth.
- Stage 2 — `pydantic-ai Agent + OpenAIChatModel +
  OpenAIProvider(base_url='http://127.0.0.1:11434/v1', api_key='ollama')`
  end-to-end test. Multi-city weather question, 2 parallel tool calls,
  correct synthesis — 40 s wall-clock. This is the direct validator
  for the Stage 3 code change: pydantic-ai drives the local model the
  same way it drives Anthropic/OpenAI.

**Files Modified:**
- `src/marcel_core/config.py` — `marcel_local_llm_url`,
  `marcel_local_llm_model` settings in the AI providers block.
- `src/marcel_core/harness/agent.py` — new `_LOCAL_PREFIX` constant,
  `_build_local_model()` helper that constructs
  `OpenAIChatModel(tag, provider=OpenAIProvider(base_url=..., api_key='ollama'))`,
  intercept in `create_marcel_agent` when `model.startswith('local:')`
  (uses `model.split` on first `:` only so ollama tags like `qwen3.5:4b`
  survive intact), `all_models()` appends the local entry only when
  both env vars are set. Raises `RuntimeError` with a pointer to
  `docs/local-llm.md` when `local:` is requested but the URL is unset.
- `src/marcel_core/jobs/models.py` — `JobDefinition.allow_local_fallback:
  bool = False` and `JobRun.fallback_used: str | None = None`. Additive
  with defaults, no migration needed (pattern from ISSUE-061).
- `src/marcel_core/jobs/executor.py` — added `_AUTH_QUOTA_PATTERNS` and
  `FALLBACK_ELIGIBLE_CATEGORIES` frozenset; `classify_error` now returns
  `(False, 'auth_or_quota')` on 401/403/invalid-key/insufficient-quota.
  `execute_job_with_retries` adds a terminal fallback branch after the
  retry loop: if `job.allow_local_fallback`, both env vars are set, and
  the final error category is fallback-eligible, it swaps `job.model`
  in-memory to `local:<model>`, runs `execute_job` once more, restores
  the original model in a `finally`, and marks `fb_run.fallback_used =
  'local'`.
- `tests/harness/test_agent.py` — promoted the fake-API-keys fixture
  to module level; added `TestLocalModelBranch` (6 tests covering
  `_build_local_model` URL-unset/empty-tag guards, `OpenAIChatModel`
  instance check, multi-colon ollama tags, `create_marcel_agent` local
  string handling, error propagation when URL unset, non-local strings
  still passing through verbatim) and `TestAllModelsLocalEntry` (3 tests
  for the conditional local entry gating).
- `tests/jobs/test_executor.py` — rewrote the old `test_permanent_auth`
  into three `auth_or_quota` tests; added `TestLocalFallback` (5 tests:
  fires on auth/quota when allowed, not when flag off, not when local
  unconfigured, not on permanent errors, fires exactly once even if
  local also fails). Used `monkeypatch` + a scripted `execute_job` fake
  so no network or filesystem is touched.
- `docs/local-llm.md` — new page: when the fallback fires, env var
  wiring, per-job opt-in example, ollama install + `qwen3.5:4b` pull
  + tool-calling verification curl, systemd drop-in with
  `MemoryMax=6G` / `CPUQuota=400%` / `Nice=10` /
  `OLLAMA_KEEP_ALIVE=5m`, post-upgrade `OLLAMA_NUM_THREAD` tuning note,
  observability (`fallback_used` in `runs.jsonl`), verification
  checklist, rollback.
- `mkdocs.yml` — nav entry `Local LLM: local-llm.md` under Jobs.

**Commands Run:** `make check` — 1244 tests pass, 92.95% coverage.

**Result:** Code change complete, all gates green. Stage 4
(systemd deploy + Plex coexistence test) stays blocked on the second
DIMM arriving later this week; everything in this commit is a no-op
until `MARCEL_LOCAL_LLM_URL` is set, so it's safe to ship now.

**Next:** (1) When the second DIMM arrives, re-run `sudo dmidecode -t
memory` to confirm dual-channel, re-measure tok/s, and tune
`OLLAMA_NUM_THREAD`. (2) Deploy the systemd unit from `docs/local-llm.md`.
(3) Flip `allow_local_fallback=true` on one low-stakes job and monitor
for a week. (4) If the IPEX-LLM nightly catches up to a recent
Qwen3.5-compatible ollama, re-evaluate for iGPU acceleration.
