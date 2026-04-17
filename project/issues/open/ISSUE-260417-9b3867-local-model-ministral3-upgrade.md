# ISSUE-9b3867: Upgrade local fallback model to Mistral Ministral 3 (14B + 8B)

**Status:** Open
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
- [ ] Verify `ollama --version` ≥ 0.13.1 on the host (needed for Ministral 3 support); note the version in the Implementation Log
- [ ] `ollama pull ministral-3:14b` and `ollama pull ministral-3:8b`
- [ ] Run the tool-calling curl from [docs/local-llm.md:107-125](../../../docs/local-llm.md#L107-L125) against both models; confirm `finish_reason: tool_calls` with well-formed JSON
- [ ] Update `.env.local`: set `MARCEL_LOCAL_LLM_MODEL=ministral-3:14b` and `MARCEL_FALLBACK_MODEL=local:ministral-3:14b`
- [ ] Rewrite the "Pull the model" section of [docs/local-llm.md](../../../docs/local-llm.md) (lines 90–99) to lead with `ministral-3:14b`, mention the 8B understudy, note the Ollama ≥ 0.13.1 requirement, update RAM/disk figures
- [ ] Update the systemd override example in [docs/local-llm.md](../../../docs/local-llm.md) (lines 137–151) with 10G/12G memory caps, `OLLAMA_NUM_THREAD=12`, `KEEP_ALIVE=10m`
- [ ] Update the example `.env.local` blocks in [docs/model-tiers.md](../../../docs/model-tiers.md) (lines ~214, 217, 228, 232) to use the new model tag
- [ ] Straggler grep: `rg 'qwen3\.5:4b' docs/ .claude/ ~/.marcel/ README.md SETUP.md mkdocs.yml` — update any other live references
- [ ] Smoke test: `make serve`, trigger one turn through the Tier-3 fallback path, confirm the local model responds and `runs/<user>.jsonl` logs `fallback_used: local` correctly
- [ ] `make check` passes (no source-code changes expected, but run it)

## Relationships
- Depends on: none
- Blocks: the phase-2 follow-up issue that pins the good-morning job to `local:ministral-3:14b`

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
