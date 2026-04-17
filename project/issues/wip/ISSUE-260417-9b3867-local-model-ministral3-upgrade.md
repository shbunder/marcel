# ISSUE-9b3867: Upgrade local fallback model to Mistral Ministral 3 (14B + 8B)

**Status:** WIP
**Created:** 2026-04-17
**Assignee:** Shaun
**Priority:** Medium
**Labels:** ops, config, docs

## Capture
**Original request:** "I now have 2x16gb ram on this device, can you do research what the best model would be to install locally: it should run the jobs, be used as a fallback model for marcel, maybe even used in some cases for real responding (especially the good morning job requires talking)." — follow-up: *"what about mistral models, I have a weak spot for european independence"*

**Follow-up Q&A:**
- *"Good morning requires talking" means?* → **Natural conversational text** (markdown → Telegram HTML). No TTS / voice pipeline in scope.
- *Role for the local model?* → Run scheduled jobs locally, quality Tier-3 fallback, **maybe also Tier-1**.
- *"What about Mistral Small 4 or quantizations?"* → Investigated. Mistral Small 4 is 119B MoE; does not fit 30 GiB at any reasonable quant, and Ollama/llama.cpp support was "not yet finalized" at its March 2026 launch. Out of reach on this hardware today.

**Resolved intent:** The host went from "small-RAM 4B-class local model as last-ditch apology voice" to "32 GiB dual-channel CPU host that can actually run a capable local agent." We replace `qwen3.5:4b` with **Ministral 3 14B** (`ministral-3:14b`), a December-2025 French dense model explicitly marketed for agentic function-calling — matching the user's European-sovereignty preference and Marcel's heavy chained-tool-use pattern. The 8B sibling (`ministral-3:8b`) comes along as a faster understudy. The actual good-morning job pin is deferred to a follow-up issue after interactive turn-mode is verified.

## Description

Phase 1 of a two-phase upgrade. This issue covers the configuration and documentation changes required to flip Marcel's local fallback from `qwen3.5:4b` to Ministral 3 14B. Phase 2 (a separate issue) will pin the good-morning job to the new local model once interactive behaviour is verified on real hardware.

### Hardware (verified in conversation)
- Intel Core Ultra 5 125H (Meteor Lake, 14 cores / 18 threads)
- Intel Arc integrated GPU, no NVIDIA dGPU
- 30 GiB RAM, dual-channel (prerequisite per [docs/local-llm.md:170-171](../../../docs/local-llm.md#L170-L171))
- Ollama already installed

### Why Ministral 3 14B over the alternatives
- **European open-weights (Apache 2.0)** — satisfies user's explicit sovereignty preference.
- **Native function-calling + JSON output** — Marcel's critical axis is tool-use, not raw reasoning.
- **Fits comfortably** (~9 GiB Q4_K_M loaded) with generous headroom in 30 GiB.
- **256k context window** — well beyond Marcel's ~8–12k typical prompt size.
- Newer than Mistral Nemo (which scored a measured 92.5% on the jdhodges 2026 tool-calling eval — strongest empirical baseline on any Mistral; Ministral 3 inherits that training lineage).
- **Not Mistral Small 4**: 119B MoE doesn't fit on this hardware at any reasonable quant.
- **Not Qwen3 30B-A3B**: raw-performance winner (MoE, 12–15 tok/s CPU) but Chinese-origin — kept as Plan C.

Full rationale and the Plan B / Plan C fallbacks live in the approved plan file: `/home/shbunder/.claude/plans/i-now-have-2x16gb-buzzing-simon.md`.

### Out of scope for this issue
- **Pinning the good-morning job to `local:ministral-3:14b`** — follow-up issue after verification.
- **Ollama pull and systemd override edits** — those happen on the host; this issue delivers the *documentation* and the `.env.local` flip, not the infra mutation.
- **Intel Arc / IPEX-LLM acceleration** — experimental, separate investigation.
- **TTS / voice loop.** Confirmed not in scope.

## Tasks

### Host-side (user to run outside this commit, in order)
- [ ] Verify `ollama --version` ≥ 0.13.1 on the host (needed for Ministral 3 support); upgrade if older
- [ ] `ollama pull ministral-3:14b` and `ollama pull ministral-3:8b`
- [ ] Run the tool-calling curl from [docs/local-llm.md](../../../docs/local-llm.md) against both models; confirm `finish_reason: tool_calls` with well-formed JSON
- [ ] Update `.env.local`: set `MARCEL_LOCAL_LLM_MODEL=ministral-3:14b` and `MARCEL_FALLBACK_MODEL=local:ministral-3:14b` **(do this only after the tool-calling curl passes — flipping sooner points the fallback chain at an unserved tag)**
- [ ] Update `/etc/systemd/system/ollama.service.d/override.conf` with the 10G/12G caps and `OLLAMA_NUM_THREAD=12`, then `sudo systemctl daemon-reload && sudo systemctl restart ollama`
- [ ] Smoke test: `make serve`, trigger one turn through the Tier-3 fallback path, confirm the local model responds and `runs/<user>.jsonl` logs `fallback_used: "local"` correctly

### Codebase (delivered in this issue)
- [✓] Rewrite the "Pull the model" section of [docs/local-llm.md](../../../docs/local-llm.md) to lead with `ministral-3:14b`, mention the 8B understudy, note the Ollama ≥ 0.13.1 requirement, update RAM/disk figures
- [✓] Update the systemd override example in [docs/local-llm.md](../../../docs/local-llm.md) with 10G/12G memory caps, `OLLAMA_NUM_THREAD=12`, `KEEP_ALIVE=10m`
- [✓] Update the example `.env.local` blocks in [docs/model-tiers.md](../../../docs/model-tiers.md) (examples + behaviour-matrix rows) to use the new model tag
- [✓] Update [README.md](../../../README.md) and [SETUP.md](../../../SETUP.md) references caught by the straggler grep
- [✓] Straggler grep: `rg 'qwen3\.5:4b' docs/ .claude/ ~/.marcel/ README.md SETUP.md mkdocs.yml` — update any other live references (excludes tests + closed-issue history + docstring illustrative examples, by policy)
- [ ] `make check` passes (no source-code changes expected, but run it)

## Relationships
- Depends on: none
- Blocks: the phase-2 follow-up issue that pins the good-morning job to `local:ministral-3:14b`

## Implementation Log

### 2026-04-17 — Docs rewrite for Ministral 3 14B

**Action:** Updated the recommended-model section of [docs/local-llm.md](../../../docs/local-llm.md) to lead with `ministral-3:14b` and the `ministral-3:8b` understudy; kept the legacy `qwen3.5:4b` reference as a short note for users with older setups. Bumped the systemd override example from 5G/6G/400% to 10G/12G/800% with `OLLAMA_NUM_THREAD=12` and `KEEP_ALIVE=10m`; folded the "post-upgrade thread tuning" section into a tighter paragraph that no longer talks about a 4-thread baseline. Updated the verify-tool-calling curl to the new model tag.

Updated examples in [docs/model-tiers.md](../../../docs/model-tiers.md) (cloud+local-explain, local-dominant, behaviour-matrix rows, known-limitation note). Straggler grep turned up live references in [README.md](../../../README.md) and [SETUP.md](../../../SETUP.md) that were not in the original plan; updated both.

**Files Modified:**
- `docs/local-llm.md` — env table example, `.env.local` example, list_models example, "Pull the model" section, verify curl, systemd override, thread-tuning section
- `docs/model-tiers.md` — two behaviour-matrix rows, two example config blocks, one known-limitations reference
- `README.md` — single-line example in the model-fallback paragraph
- `SETUP.md` — two rows in the env-var table
- `project/issues/open/… → project/issues/wip/…` — move + status flip

**Files intentionally NOT touched:**
- `.env.local` — deferred to host-side tasks. Flipping the env vars before the user has `ollama pull`ed Ministral 3 would point the fallback chain at an unserved tag. Also blocked by `.claude/hooks/guard-restricted.py` which is appropriate — any future `.env.local` change for this issue is a user-side action, not a commit.
- `~/.marcel/jobs/*/JOB.md` (news-sync, bank-sync, good-morning, test-signal) — four job definitions currently pin `local:qwen3.5:4b`. They keep working against the still-pulled 4B. Migrating them is explicitly phase-2 scope per the approved plan; touching them here would mute the planned verification step.
- Tests (`tests/harness/*`, `tests/jobs/*`, `tests/tools/*`) — use `qwen3.5:4b` as a stable fixture tag. Not a recommendation, not user-facing, and churning them across ~15 files would add no value.
- Docstring illustrative examples in `src/marcel_core/harness/agent.py:87` and `src/marcel_core/config.py:75,82` — show the format of a tag-with-colon, not a product recommendation. Leaving as-is.
- Closed issue history (`project/issues/closed/ISSUE-070-*.md`, `ISSUE-260416-b95ac5-*.md`) — never edited.

**Commands run:** none yet — `make check` will run via the pre-commit hook when this gets staged.

**Next:** User runs the host-side task list (ollama pull, curl verify, `.env.local` flip, systemd override, smoke test) and reports back. Then `/finish-issue` closes this.

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
